from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from .domain import SYSTEM, DomainError, now, encode
from .inventory import device_status
from .policy import evaluate_lease

log=logging.getLogger("hive.worker")


class Worker:
    def __init__(self,services):
        self.s=services
        self.stop=threading.Event()
        self.probe_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="hive-probes")
        self.probe_future=None
        self.last_maintenance=0
        self.preflight_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix="hive-preflight")
        self.control_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix="hive-control")
        self.preflights={}
        self.controls={}
        self.control_last={}
        self.benchmark_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="hive-benchmark")
        self.benchmark_future=None
        self.workflow_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='hive-workflows')
        self.workflow_futures={}
        self.workflow_last={}

    def collect_loop(self):
        with ThreadPoolExecutor(max_workers=self.s.settings.ssh_workers,thread_name_prefix="hive-collect") as pool:
            futures={}
            last={}
            while not self.stop.is_set():
                try:
                    for n in self.s.inventory.list_nodes():
                        node_id=n["id"]
                        f=futures.get(node_id)
                        if f is not None and f.done():
                            try:
                                f.result()
                            except Exception as exc:
                                log.warning("collect node=%s error=%s",node_id,type(exc).__name__)
                            del futures[node_id]
                        if node_id not in futures and time.monotonic()-last.get(node_id,0)>=self.s.settings.sample_seconds:
                            futures[node_id]=pool.submit(self.s.telemetry.collect_node,node_id)
                            last[node_id]=time.monotonic()
                except Exception as exc:
                    log.error("collector error=%s",type(exc).__name__)
                self.stop.wait(1)

    def preflight(self,req):
        rid,epoch=req["id"],req["version"]
        devices=self.s.resources.devices(rid)
        try:
            for node_id in sorted({d["node_id"] for d in devices}):
                self.s.telemetry.collect_node(node_id)
            devices=self.s.resources.devices(rid)
            identity_fields=("boot_id","command_id","chip_id","logical_id","config_version")
            expected={d["id"]:tuple(d.get(k) for k in identity_fields) for d in devices}
            for d in devices:
                d=dict(d)
                d.pop("request_id",None)
                if device_status(d,stale_seconds=self.s.settings.stale_seconds)!="available":
                    raise DomainError("交付复验发现设备已占用、故障或缺测")
            self.s.probes.check_selection(devices,require_interconnect=req["spec"].get("require_interconnect",False),
                                          shared_storage_id=req["spec"].get("shared_storage_id"))
            # Network/shared storage probes may take minutes. Refresh after them,
            # then recheck under the allocator's database transaction.
            for node_id in sorted({d["node_id"] for d in devices}):
                self.s.telemetry.collect_node(node_id)
            refreshed=self.s.resources.devices(rid)
            if {d["id"]:tuple(d.get(k) for k in identity_fields) for d in refreshed}!=expected:
                raise DomainError("探测期间节点重启、设备映射或配置改变")
            if not self.s.resources.deliver(rid,epoch,expected_devices=expected):
                raise DomainError("申请已取消或分配版本改变")
        except Exception as exc:
            reason=str(exc) if isinstance(exc,DomainError) else "交付检查失败: "+type(exc).__name__
            self.s.resources.abort_reservation(rid,epoch,reason)

    @staticmethod
    def drain(futures):
        for key,future in list(futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception as exc:
                    log.error("operation request=%s error=%s",key,type(exc).__name__)
                del futures[key]

    def control_request(self,request_id,execution_id=None):
        if execution_id:
            self.s.execution.tick_one(execution_id)
        if self.s.resources.get(request_id)["status"]=="RELEASING":
            self.s.cleanup.run(request_id)

    def tick(self):
        s=self.s
        self.drain(self.preflights)
        self.drain(self.controls)
        self.drain(self.workflow_futures)
        if hasattr(s, 'workflows'):
            spaces=s.db.all("SELECT id FROM workflow_spaces WHERE status!='CLOSED' ORDER BY created_at")
            spaces.sort(key=lambda row:self.workflow_last.get(row['id'],0))
            for row in spaces:
                if row['id'] not in self.workflow_futures and len(self.workflow_futures)<4:
                    self.workflow_futures[row['id']]=self.workflow_pool.submit(s.workflows.tick_space,row['id'])
                    self.workflow_last[row['id']]=time.monotonic()
        if self.benchmark_future is not None and self.benchmark_future.done():
            try:
                self.benchmark_future.result()
            except Exception as exc:
                log.error("compute benchmark error=%s",type(exc).__name__)
            self.benchmark_future=None
        if self.benchmark_future is None and hasattr(s, 'compute_benchmark'):
            pending_benchmarks=s.compute_benchmark.pending()
            if pending_benchmarks:
                self.benchmark_future=self.benchmark_pool.submit(s.compute_benchmark.run,pending_benchmarks[0])
        s.resources.expire_queued()
        if time.monotonic()-self.last_maintenance>=60:
            s.reporting.maintain()
            self.last_maintenance=time.monotonic()
        if self.probe_future is not None and self.probe_future.done():
            try:
                self.probe_future.result()
            except Exception as exc:
                log.warning("admission probe error=%s",type(exc).__name__)
            self.probe_future=None
        if self.probe_future is None:
            pending=s.db.one("""SELECT id FROM nodes WHERE probe_requested=TRUE AND status='ok'
                             AND EXISTS(SELECT 1 FROM devices d WHERE d.node_id=nodes.id)
                             ORDER BY created_at LIMIT 1""")
            if pending:
                self.probe_future=self.probe_pool.submit(s.probes.run,pending["id"])
        # Reconcile pre-existing reservations before issuing new ones.
        for row in s.db.all("SELECT id FROM resource_requests WHERE status='RESERVED' ORDER BY created_at"):
            if row["id"] not in self.preflights and len(self.preflights)<2:
                self.preflights[row["id"]]=self.preflight_pool.submit(self.preflight,s.resources.get(row["id"]))
        pools=set()
        for row in s.db.all("SELECT id FROM resource_requests WHERE status='QUEUED' ORDER BY created_at"):
            req=s.resources.get(row["id"])
            generation=(req["spec"].get("vendor","ascend"),req["spec"].get("device_kind","npu"),req["spec"]["generation"])
            # Immediate attempts do not join (or block) the generation FIFO.
            # They still use the same transactional reservation and card locks.
            if req["spec"].get("queue",True):
                if generation in pools:
                    continue
                pools.add(generation)
            if len(self.preflights)>=2:
                break
            reserved=s.resources.reserve(req["id"])
            if reserved:
                self.preflights[req["id"]]=self.preflight_pool.submit(self.preflight,reserved)
        for row in s.db.all("SELECT id FROM resource_requests WHERE status='ACTIVE' AND purpose='debug'"):
            req=s.resources.get(row["id"])
            devices=s.resources.devices(req["id"])
            if req["spec"].get("vendor","ascend")!="ascend":
                continue  # Other hardware requires its explicitly registered reclamation policy.
            decision,reason,evidence=evaluate_lease(req,devices,s.telemetry.window(devices),now(),s.settings.sample_seconds)
            with s.db.transaction() as c:
                c.execute("SELECT decision,reason FROM lease_decisions WHERE request_id=%s",(req["id"],))
                old=c.fetchone()
                c.execute("""INSERT INTO lease_decisions VALUES (%s,%s,%s,%s,%s)
                          ON DUPLICATE KEY UPDATE decision=VALUES(decision),reason=VALUES(reason),
                          evidence=VALUES(evidence),checked_at=VALUES(checked_at)""",
                          (req["id"],decision,reason,encode(evidence),now()))
                if not old or old["decision"]!=decision or old["reason"]!=reason:
                    s.db.audit(c,SYSTEM,"lease."+decision,req["id"],{"reason":reason,"evidence":evidence})
            if decision=="cleanup":
                s.resources.release(req["id"],SYSTEM)
        rows=s.db.all("""SELECT r.id,e.id AS execution_id FROM resource_requests r LEFT JOIN executions e ON e.request_id=r.id
                       WHERE r.status='RELEASING' OR e.status NOT IN ('SUCCEEDED','FAILED','CANCELLED')
                       ORDER BY COALESCE(r.release_started_at,r.created_at)""")
        # Rotate polls across all active requests; four long-running early tasks
        # must not starve later tasks or returns after each short status poll.
        rows.sort(key=lambda row:self.control_last.get(row["id"],0))
        active_ids={row["id"] for row in rows}
        self.control_last={key:value for key,value in self.control_last.items() if key in active_ids}
        for row in rows:
            if row["id"] not in self.controls and row["id"] not in self.preflights and len(self.controls)<4:
                self.controls[row["id"]]=self.control_pool.submit(self.control_request,row["id"],row["execution_id"])
                self.control_last[row["id"]]=time.monotonic()

    def run(self):
        # A session advisory lock elects one worker; connection loss causes fail-stop.
        connection=self.s.db.connect()
        collector=None
        try:
            with connection.cursor() as c:
                c.execute("SELECT GET_LOCK('hive.worker',0) AS acquired")
                if c.fetchone()["acquired"]!=1:
                    raise RuntimeError("Another Hive worker is running")
            collector=threading.Thread(target=self.collect_loop,name="hive-collector",daemon=True)
            collector.start()
            while not self.stop.is_set():
                connection.ping(reconnect=False)
                try:
                    self.tick()
                except Exception as exc:
                    log.error("worker tick error=%s",type(exc).__name__,exc_info=True)
                self.stop.wait(2)
        finally:
            self.stop.set()
            self.s.transport.close()
            self.s.telemetry_transport.close()
            if hasattr(self.s,'benchmark_transport'):
                self.s.benchmark_transport.close()
            if collector:
                collector.join(timeout=30)
            self.probe_pool.shutdown(wait=True,cancel_futures=True)
            self.preflight_pool.shutdown(wait=True,cancel_futures=True)
            self.control_pool.shutdown(wait=True,cancel_futures=True)
            self.benchmark_pool.shutdown(wait=True,cancel_futures=True)
            self.workflow_pool.shutdown(wait=True,cancel_futures=True)
            connection.close()
