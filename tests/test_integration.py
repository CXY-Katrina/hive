"""Uses a separate disposable MySQL database; never touches configured production tables."""
import base64
import bcrypt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import os
import unittest
from types import SimpleNamespace
import pymysql
from fastapi.testclient import TestClient
from hive.api import create_app
from hive.config import Settings
from hive.db import Database
from hive.domain import DomainError, Snapshot, DeviceSample, now, SYSTEM
from hive.identity import Identity
from hive.inventory import Inventory, device_status
from hive.metrics import MetricCatalog
from hive.resources import ResourceService
from hive.schemas import ResourceSpec
from hive.telemetry import Telemetry


@unittest.skipUnless(os.getenv("HIVE_TEST_MYSQL_PORT"),"Set HIVE_TEST_MYSQL_PORT for isolated MySQL tests")
class MySQLIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hive.domain import uid
        cls.name="hive_test_"+uid().replace("-","")
        cls.settings=Settings(mysql_port=int(os.environ["HIVE_TEST_MYSQL_PORT"]),
              mysql_user=os.getenv("HIVE_TEST_MYSQL_USER","root"),mysql_password=os.getenv("HIVE_TEST_MYSQL_PASSWORD",""),
              mysql_database=cls.name,secret_key=base64.urlsafe_b64encode(b"x"*32).decode(),cookie_secure=False,
              origin="http://testserver", admin_password_hash=bcrypt.hashpw(b'test-admin', bcrypt.gensalt(rounds=4)).decode())
        connection=pymysql.connect(host="127.0.0.1",port=cls.settings.mysql_port,user=cls.settings.mysql_user,
                                   password=cls.settings.mysql_password,autocommit=True)
        with connection.cursor() as c:
            c.execute("CREATE DATABASE "+cls.name+" CHARACTER SET utf8mb4")
        connection.close()
        cls.db=Database(cls.settings)
        cls.db.migrate()

    @classmethod
    def tearDownClass(cls):
        assert cls.name.startswith("hive_test_") and cls.name[10:].isalnum()
        with cls.db.transaction() as c:
            c.execute("DROP DATABASE "+cls.name)

    def setUp(self):
        with self.db.transaction() as c:
            for table in ("process_events","device_rollups","worker_state","lease_decisions","audit_events","execution_nodes","executions","connectivity_checks",
                          "allocation_devices","device_ownership","resource_requests","device_samples","devices","nodes","sessions","users"):
                c.execute("DELETE FROM "+table)
        self.identity=Identity(self.db,self.settings)
        self.inventory=Inventory(self.db,self.settings)
        self.catalog=MetricCatalog()
        self.telemetry=Telemetry(self.db,self.inventory,{},self.catalog,self.settings)
        self.resources=ResourceService(self.db,self.settings)
        _,self.admin=self.identity.login("admin", "test-admin")
        _,self.alice=self.identity.login("alice")
        _,self.bob=self.identity.login("bob")
        for member in (self.alice, self.bob):
            self.identity.permissions(self.admin, member.id, {'can_request': True, 'can_view_credentials': False})

    def node(self,host="10.0.0.1",cards=4):
        n=self.inventory.create(self.admin,{"name":host,"host":host,"password":"test-secret","generation":"A2","model":"test-4"})
        snapshot=Snapshot([DeviceSample(str(i),str(i),"0",str(i),64*1024**3,0,0,"OK",
                                     process_complete=True) for i in range(cards)],"test-boot",now())
        self.telemetry.ingest(n["id"],snapshot)
        return self.inventory.get(n["id"])

    def test_empty_database_result_is_a_list_for_idle_worker_sorting(self):
        rows = self.db.all("SELECT id FROM resource_requests WHERE 1=0")
        self.assertEqual(rows, [])
        rows.sort(key=lambda row: row['id'])

    def test_remove_node_retains_history_revokes_credentials_and_allows_readmission(self):
        from hive.domain import uid
        node = self.node()
        with self.assertRaises(DomainError):
            self.inventory.remove(node['id'], self.alice)
        self.inventory.remove(node['id'], self.admin)
        self.assertEqual(self.inventory.list_nodes(), [])
        saved = self.db.one('SELECT * FROM nodes WHERE id=%s', (node['id'],))
        self.assertIsNotNone(saved['deleted_at'])
        self.assertEqual(saved['password_cipher'], '')
        self.assertTrue(saved['maintenance'])
        self.assertTrue(self.db.all('SELECT * FROM device_samples'))
        with self.assertRaises(DomainError):
            self.inventory.connection(node['id'])
        with self.assertRaises(DomainError):
            self.inventory.update(node['id'], self.admin, {'maintenance': False})
        # In-flight samples must not restore the removed node.
        self.telemetry.ingest(node['id'], Snapshot([DeviceSample('0','0','0','0',64*1024**3,0,0,'OK',process_complete=True)], 'test-boot', now()))
        self.assertEqual(self.db.one('SELECT status FROM nodes WHERE id=%s', (node['id'],))['status'], 'removed')
        req = self.resources.create(self.alice, ResourceSpec(generation='A2').model_dump(), uid())
        self.assertIsNone(self.resources.reserve(req['id']))
        replacement = self.node()
        self.assertNotEqual(replacement['id'], node['id'])
        self.assertEqual(len(self.inventory.list_nodes()), 1)

    def test_remove_node_rejects_live_allocations_and_benchmarks(self):
        from hive.domain import uid, encode
        node = self.node()
        request = self.resources.create(self.alice, ResourceSpec(generation='A2').model_dump(), uid())
        self.resources.reserve(request['id'])
        with self.assertRaisesRegex(DomainError, '有效资源申请'):
            self.inventory.remove(node['id'], self.admin)
        other = self.node(host='10.0.0.2')
        with self.db.transaction() as cursor:
            cursor.execute('UPDATE nodes SET metadata=%s WHERE id=%s', (encode({'compute_benchmark': {'status': 'RUNNING'}}), other['id']))
        with self.assertRaisesRegex(DomainError, '算力测试'):
            self.inventory.remove(other['id'], self.admin)

    def test_detected_model_replaces_manual_label(self):
        node = self.node()
        self.inventory.record_probe(node['id'], metadata={'hardware_profile': {'system_product': 'AC222'}})
        self.assertEqual(self.inventory.get(node['id'])['model'], 'AC222')

    def test_server_model_label_and_legacy_model_both_match_resources(self):
        from hive.domain import uid
        from hive.inventory import model_label
        node = self.node()
        original = 'Atlas 800I A3 / IT22HMDA_4_S'
        with self.db.transaction() as c:
            c.execute("UPDATE nodes SET model=%s,generation='A3' WHERE id=%s", (original, node['id']))
        listed = self.inventory.get(node['id'])
        self.assertEqual(listed['model'], original)
        self.assertEqual(listed['model_label'], 'Atlas 800I A3')
        for model in ('Atlas 800I A3', original):
            request = self.resources.create(self.alice, ResourceSpec(generation='A3', model=model).model_dump(), uid())
            self.assertIsNotNone(self.resources.reserve(request['id']))
        wrong = self.resources.create(self.alice, ResourceSpec(generation='A3', model='Atlas 800I').model_dump(), uid())
        self.assertIsNone(self.resources.reserve(wrong['id']))
        self.assertEqual(model_label({'model': 'Server / Custom'}), 'Server / Custom')
        self.assertEqual(model_label({'model': 'Server / Board-X', 'metadata': {'hardware_profile': {'board_product': 'Board-X'}}}), 'Server')

    def test_compute_spec_requires_admin_current_soc_and_keeps_source(self):
        n = self.node()
        spec = {'compute_spec': {'fp16_tflops_per_module': 752, 'source': 'Verified vendor sheet'}}
        with self.assertRaises(DomainError): self.inventory.update(n['id'], self.alice, spec)
        with self.assertRaises(DomainError): self.inventory.update(n['id'], self.admin, spec)
        with self.db.transaction() as c:
            c.execute("UPDATE nodes SET generation='A3' WHERE id=%s", (n['id'],))
        self.inventory.record_probe(n['id'], metadata={'hardware_profile': {
            'quality': 'ok', 'soc_versions': ['Ascend910_9362'], 'boot_id': n['boot_id']}})
        updated = self.inventory.update(n['id'], self.admin, spec)
        confirmed = updated['metadata']['compute_spec']
        self.assertEqual(confirmed['fp16_tflops_per_module'], 752)
        self.assertEqual(confirmed['confirmed_soc_versions'], ['Ascend910_9362'])
        self.assertEqual(confirmed['source'], 'Verified vendor sheet')
        with self.db.transaction() as c:
            c.execute("UPDATE nodes SET boot_id='changed' WHERE id=%s", (n['id'],))
        self.assertEqual(self.inventory.get(n['id'])['metadata']['hardware_profile']['quality'], 'unknown')
        with self.assertRaises(DomainError): self.inventory.update(n['id'], self.admin, spec)
        updated = self.inventory.update(n['id'], self.admin, {'clear_compute_spec': True})
        self.assertNotIn('compute_spec', updated['metadata'])

    def request(self,actor=None,**kwargs):
        spec=ResourceSpec(generation="A2",**kwargs).model_dump()
        from hive.domain import uid
        return self.resources.create(actor or self.alice,spec,uid())

    def test_username_login_idempotent_case_and_no_password(self):
        token,a=self.identity.login(" alice ")
        self.assertEqual(a.id,self.alice.id)
        _,capital=self.identity.login("Alice")
        self.assertNotEqual(capital.id,a.id)
        self.identity.logout(token,a)
        with self.assertRaises(DomainError):
            self.identity.current(token)

    def test_concurrent_first_login(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            actors=list(pool.map(lambda _:self.identity.login("new-user")[1],range(6)))
        self.assertEqual(len({a.id for a in actors}),1)

    def test_real_concurrent_card_lock(self):
        self.node(cards=2)
        a=self.request(cards_per_node=2)
        b=self.request(self.bob,cards_per_node=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.resources.reserve,[a["id"],b["id"]]))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM device_ownership")["n"],2)

    def test_partial_whole_and_all_or_nothing(self):
        self.node(cards=4)
        a=self.request(cards_per_node=2)
        self.resources.reserve(a["id"])
        b=self.request(self.bob,mode="whole")
        self.assertIsNone(self.resources.reserve(b["id"]))
        c=self.request(self.bob,cards_per_node=2)
        self.assertIsNotNone(self.resources.reserve(c["id"]))
        d=self.request(machine_count=2)
        self.assertIsNone(self.resources.reserve(d["id"]))

    def test_idempotency_conflicting_body(self):
        spec=ResourceSpec(generation="A2").model_dump()
        a=self.resources.create(self.alice,spec,"one")
        b=self.resources.create(self.alice,spec,"one")
        self.assertEqual(a["id"],b["id"])
        with self.assertRaises(DomainError):
            self.resources.create(self.alice,{**spec,"cards_per_node":2},"one")

    def test_external_usage_unknown_and_stale_excluded(self):
        n=self.node()
        d=n["devices"][0]
        with self.db.transaction() as c:
            c.execute("UPDATE devices SET ai_core=80 WHERE id=%s",(d["id"],))
        r=self.request(cards_per_node=4)
        self.assertIsNone(self.resources.reserve(r["id"]))
        with self.db.transaction() as c:
            c.execute("UPDATE devices SET ai_core=0,quality='unknown' WHERE id=%s",(d["id"],))
        self.assertIsNone(self.resources.reserve(r["id"]))

    def test_protection_and_release_epoch_guard(self):
        self.node()
        req=self.request()
        reserved=self.resources.reserve(req["id"])
        self.resources.deliver(req["id"],reserved["version"])
        active=self.resources.get(req["id"])
        self.assertEqual((active["protected_until"]-active["delivered_at"]).total_seconds(),1800)
        with self.assertRaises(DomainError):
            self.resources.release(req["id"],self.bob)
        self.resources.release(req["id"],self.alice)
        self.assertFalse(self.resources.finish_release(req["id"],reserved["version"]-1))
        self.assertTrue(self.resources.finish_release(req["id"],reserved["version"]))
        self.assertEqual(self.resources.get(req["id"])["status"],"RELEASED")

    def test_inventory_credentials_not_in_lists(self):
        n=self.node()
        self.assertNotIn("password",n)
        self.assertNotIn("password_cipher",n)
        with self.assertRaises(DomainError):
            self.inventory.credentials(n["id"],self.alice)
        req=self.request()
        reserved=self.resources.reserve(req["id"])
        self.resources.deliver(req["id"],reserved["version"])
        with self.assertRaises(DomainError):
            self.inventory.credentials(n["id"],self.alice)
        self.identity.permissions(self.admin, self.alice.id, {'can_request': True, 'can_view_credentials': True})
        self.assertEqual(self.inventory.credentials(n["id"],self.alice)["password"],"test-secret")

    def test_http_sessions_and_owner_tampering(self):
        services=SimpleNamespace(db=self.db,settings=self.settings,identity=self.identity,inventory=self.inventory,
              catalog=self.catalog,resources=self.resources,telemetry=self.telemetry,execution=None)
        with TestClient(create_app(services,self.settings)) as client:
            self.assertEqual(client.get("/api/nodes").status_code,401)
            self.assertEqual(client.post("/api/session",json={"username":"alice"}).status_code,200)
            result=client.post("/api/requests",json={"idempotency_key":"http","spec":{"generation":"A2"}})
            self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(result.json()["owner_user_id"],self.alice.id)
            self.assertTrue(result.json()["created_at"].endswith("Z"))
            self.assertEqual(client.post("/api/requests",json={"idempotency_key":"evil","spec":{"generation":"A2"},"owner":"bob"}).status_code,422)
            self.assertEqual(client.post("/api/session",json={"username":"bob"},headers={"Origin":"https://outside.example"}).status_code,403)
            client.delete("/api/session")
            self.assertEqual(client.get("/api/session").status_code,401)
            self.assertEqual(self.resources.get(result.json()["id"])["status"],"QUEUED")

    def test_rollup_persists_numerators_and_usage_deduplicates_nodes(self):
        from hive.reporting import Reporting
        n=self.node()
        a=self.request(cards_per_node=2)
        b=self.request(self.bob,cards_per_node=2)
        self.resources.reserve(a["id"])
        self.resources.reserve(b["id"])
        end=now().replace(second=0,microsecond=0)-timedelta(minutes=1)
        start=end-timedelta(minutes=1)
        with self.db.transaction() as c:
            c.execute("UPDATE allocation_devices SET locked_at=%s,released_at=%s",(end-timedelta(hours=1),end))
            for i in range(4):
                c.execute("""INSERT INTO device_samples (device_id,sampled_at,boot_id,ai_core,memory_used,memory_total,quality)
                          VALUES (%s,%s,'test-boot',50,1024,2048,'ok')""",(n["devices"][0]["id"],start+timedelta(seconds=i*15)))
        reporting=Reporting(self.db,self.settings)
        reporting._minute(start,end)
        rows=reporting.history(n["devices"][0]["id"])
        self.assertEqual(rows[0]["ai_core"],50)
        self.assertEqual(rows[0]["ai_valid_seconds"],60)
        usage=reporting.usage()
        self.assertEqual(usage["machine_hours"],1)
        self.assertEqual(sum(u["device_hours"] for u in usage["users"]),4)
        reporting.maintain()

    def test_idle_release_cannot_reactivate_after_signalling_begins(self):
        self.node()
        req=self.request()
        reservation=self.resources.reserve(req["id"])
        self.resources.deliver(req["id"],reservation["version"])
        self.resources.release(req["id"],SYSTEM)
        self.assertTrue(self.resources.cancel_idle_release(req["id"],reservation["version"]))
        self.resources.release(req["id"],SYSTEM)
        self.assertTrue(self.resources.begin_cleanup(req["id"],reservation["version"]))
        self.assertFalse(self.resources.cancel_idle_release(req["id"],reservation["version"]))

    def test_active_mapping_change_stays_unknown_on_repeated_samples(self):
        n=self.node()
        req=self.request(cards_per_node=4)
        self.resources.reserve(req["id"])
        for _ in range(2):
            snapshot=Snapshot([DeviceSample(str(i),str(i),'0',str(i+8),64*1024**3,0,0,'OK',process_complete=True)
                               for i in range(4)],'test-boot',now())
            self.telemetry.ingest(n['id'],snapshot)
        devices=self.inventory.get(n['id'])['devices']
        self.assertTrue(all(d['quality']=='unknown' for d in devices))
        self.assertEqual({d['logical_id'] for d in devices},{'0','1','2','3'})

    def test_deliver_rechecks_latest_occupancy_under_transaction(self):
        node=self.node()
        req=self.request()
        reservation=self.resources.reserve(req['id'])
        with self.db.transaction() as c:
            c.execute('UPDATE devices SET ai_core=99 WHERE id=%s',(self.resources.devices(req['id'])[0]['id'],))
        with self.assertRaises(DomainError):
            self.resources.deliver(req['id'],reservation['version'])
        self.assertEqual(self.resources.get(req['id'])['status'],'RESERVED')

    def test_shared_storage_candidate_filter_chooses_matching_node(self):
        from hive.domain import encode
        a=self.node('10.0.0.1')
        b=self.node('10.0.0.2')
        with self.db.transaction() as c:
            c.execute('UPDATE nodes SET mounts=%s WHERE id=%s',(encode([{'candidate_id':'shared','readable':True,'writable':True}]),b['id']))
        req=self.request(shared_storage_id='shared')
        self.resources.reserve(req['id'])
        self.assertEqual({d['node_id'] for d in self.resources.devices(req['id'])},{b['id']})

    def test_request_history_limit_preserves_every_nonterminal_request(self):
        from hive.domain import encode, uid
        self.node(cards=1)
        active=self.request()
        reservation=self.resources.reserve(active['id'])
        self.resources.deliver(active['id'],reservation['version'])
        live={active['id']}
        for status in ('QUEUED','RESERVED','RELEASING'):
            request=self.request()
            live.add(request['id'])
            with self.db.transaction() as c:
                c.execute('UPDATE resource_requests SET status=%s WHERE id=%s',(status,request['id']))
        stamp=now()
        terminal_ids=[uid() for _ in range(505)]
        spec=encode(ResourceSpec(generation='A2').model_dump())
        with self.db.transaction() as c:
            c.execute('UPDATE resource_requests SET created_at=%s',(stamp-timedelta(days=1),))
            c.executemany('''INSERT INTO resource_requests
                (id,owner_user_id,owner_name,idempotency_key,body_hash,spec,purpose,status,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,'debug',%s,%s)''',[
                (ident,self.alice.id,self.alice.username,ident,'history-fixture',spec,
                 ('RELEASED','CANCELLED','FAILED')[index%3],stamp-timedelta(seconds=index))
                for index,ident in enumerate(terminal_ids)])
        rows=self.resources.list()
        self.assertEqual(len(rows),504)
        self.assertEqual({row['id'] for row in rows},live|set(terminal_ids[:500]))
        self.assertEqual([row['created_at'] for row in rows],sorted((row['created_at'] for row in rows),reverse=True))
        current=next(row for row in rows if row['id']==active['id'])
        self.assertEqual(len(current['devices']),1)
        self.assertEqual(current['spec']['generation'],'A2')

    def test_immediate_reservation_bypasses_unsatisfied_queued_request(self):
        self.node(cards=1)
        head=self.request(cards_per_node=2,queue=True)
        self.assertIsNone(self.resources.reserve(head['id']))
        immediate=self.request(queue=False)
        self.assertIsNotNone(self.resources.reserve(immediate['id']))
        self.assertEqual(self.resources.get(head['id'])['status'],'QUEUED')
        unavailable=self.request(queue=False)
        self.assertIsNone(self.resources.reserve(unavailable['id']))
        self.assertEqual(self.resources.get(unavailable['id'])['status'],'FAILED')

    def test_confirmed_driver_baseline_tolerance_matches_allocation_and_release(self):
        from hive.cleanup import Cleanup
        from unittest.mock import Mock
        node=self.node(cards=1)
        ident=node['devices'][0]['id']
        baseline=3*1024**3
        with self.db.transaction() as c:
            c.execute('UPDATE devices SET memory_used=%s WHERE id=%s',(baseline,ident))
        self.inventory.confirm_baseline(ident,self.admin)
        with self.db.transaction() as c:
            c.execute('UPDATE devices SET memory_used=%s WHERE id=%s',(baseline+2*1024**2,ident))
        self.assertEqual(self.inventory.get(node['id'])['devices'][0]['status'],'available')
        request=self.request()
        reservation=self.resources.reserve(request['id'])
        self.assertIsNotNone(reservation)
        self.assertTrue(self.resources.deliver(request['id'],reservation['version']))
        self.resources.release(request['id'],self.alice)
        cleanup=Cleanup(self.db,Mock(),self.inventory,Mock(),self.resources)
        with self.db.transaction() as c:
            c.execute('UPDATE devices SET memory_used=%s WHERE id=%s',(baseline+2*1024**2+1,ident))
        self.assertFalse(cleanup.run(request['id']))
        self.assertEqual(self.resources.get(request['id'])['status'],'RELEASING')
        with self.db.transaction() as c:
            c.execute('UPDATE devices SET memory_used=%s WHERE id=%s',(baseline+2*1024**2,ident))
        self.assertTrue(cleanup.run(request['id']))
        self.assertEqual(self.resources.get(request['id'])['status'],'RELEASED')

    def test_explicit_node_constraint_cannot_allocate_another_available_node(self):
        first = self.node('10.0.0.1')
        second = self.node('10.0.0.2')
        body = ResourceSpec(generation='A2', target_node_ids=[second['id']]).model_dump()
        request = self.resources.create(self.alice, body, 'specific-node')
        reserved = self.resources.reserve(request['id'])
        self.assertIsNotNone(reserved)
        self.assertEqual({d['node_id'] for d in self.resources.devices(request['id'])}, {second['id']})
        self.assertNotIn(first['id'], body['target_node_ids'])
