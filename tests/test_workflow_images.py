"""Bounded image preparation at the SSH boundary, without real image pulls."""
from datetime import timedelta
import os
import unittest
from unittest.mock import patch

from hive.domain import DomainError, now
from tests.test_container_runtime import SSH, NODE, IDENTITY
from hive.domain import CommandResult
from tests import test_workflow_multinode as multinode
from tests.test_workflows import payload
from tests.workflow_remote import Remote


IMAGE = 'quay.io/ascend/vllm-ascend:latest'
DIGEST = 'sha256:'+'b'*64


class WorkflowImageTests(unittest.TestCase):
    def resolve(self,ssh,state,image=IMAGE):
        from hive.workflow_images import resolve_image
        return resolve_image(ssh,NODE,image,IDENTITY['host_boot_id'],state,lambda:None)

    def test_present_image_is_preferred_without_pull(self):
        ssh = SSH('HIVE_IMAGE '+DIGEST)
        self.assertEqual(self.resolve(ssh,{}),DIGEST)
        self.assertEqual(len(ssh.calls),1)
        self.assertNotIn('docker pull',ssh.calls[0][1])

    def test_missing_allowlisted_tag_pulls_once_then_freezes_actual_id(self):
        ssh = SSH('HIVE_IMAGE_MISSING','HIVE_IMAGE_PULL_EXIT 0','HIVE_IMAGE '+DIGEST)
        state = {}
        self.assertEqual(self.resolve(ssh,state),DIGEST)
        self.assertEqual(state['image_pull']['status'],'COMPLETED')
        self.assertIn('docker pull -- '+IMAGE,ssh.calls[1][1])
        self.assertIn('--kill-after=10s 600s',ssh.calls[1][1])
        self.assertGreaterEqual(ssh.calls[1][2],620)

    def test_other_repositories_never_pull_automatically(self):
        for image in ('ubuntu:latest','quay.io/elsewhere/vllm-ascend:latest',
                      'quay.io/ascend/vllm-ascend:latest;echo injected'):
            ssh = SSH('HIVE_IMAGE_MISSING')
            with self.subTest(image=image),self.assertRaises(DomainError) as raised:
                self.resolve(ssh,{},image)
            self.assertEqual(raised.exception.code,422)
            self.assertEqual(len(ssh.calls),1)

    def test_confirmed_pull_failure_is_terminal_and_never_retried(self):
        state = {}
        ssh = SSH('HIVE_IMAGE_MISSING','HIVE_IMAGE_PULL_EXIT 1','HIVE_IMAGE_MISSING')
        for _ in range(2):
            with self.assertRaises(DomainError) as raised:
                self.resolve(ssh,state)
            self.assertEqual(raised.exception.code,422)
        self.assertEqual(sum('docker pull' in call[1] for call in ssh.calls),1)

    def test_interrupted_pull_retains_deadline_and_restart_never_replays_it(self):
        state = {}
        ssh = SSH('HIVE_IMAGE_MISSING',TimeoutError('private network details'))
        with self.assertRaises(DomainError) as raised:
            self.resolve(ssh,state)
        self.assertEqual(raised.exception.code,503)
        self.assertNotIn('private network',str(raised.exception))
        self.assertEqual(state['image_pull']['status'],'STARTING')
        resumed = SSH('HIVE_IMAGE_MISSING')
        with self.assertRaises(DomainError) as raised:
            self.resolve(resumed,state)
        self.assertEqual(raised.exception.code,503)
        self.assertEqual(len(resumed.calls),1)
        state['image_pull']['deadline'] = (now()-timedelta(seconds=1)).isoformat()
        with self.assertRaises(DomainError) as raised:
            self.resolve(SSH('HIVE_IMAGE_MISSING'),state)
        self.assertEqual(raised.exception.code,422)


class ImageRemote(Remote):
    def __init__(self,present=False,exit_code=0,interrupt=False):
        super().__init__()
        self.present,self.exit_code,self.interrupt=present,exit_code,interrupt
        self.pulls=0
        self.bootstrap_scripts=[]
        self.on_pull=None

    def run(self,node,script,timeout=20):
        if '# HIVE_BOOTSTRAP_INSPECT' in script and not self.present:
            return CommandResult('','image missing',1)
        if '# HIVE_IMAGE_LOCAL' in script:
            return CommandResult('HIVE_IMAGE '+DIGEST if self.present else 'HIVE_IMAGE_MISSING','',0)
        if '# HIVE_IMAGE_PULL' in script:
            self.pulls+=1
            if self.on_pull:
                self.on_pull()
            if self.interrupt:
                raise TimeoutError('connection interrupted')
            self.present=self.exit_code==0
            return CommandResult('HIVE_IMAGE_PULL_EXIT '+str(self.exit_code),'',0)
        if '# HIVE_BOOTSTRAP_RUN' in script:
            self.bootstrap_scripts.append(script)
        return super().run(node,script,timeout)


@unittest.skipUnless(os.getenv('HIVE_TEST_MYSQL_PORT'),'Needs isolated MySQL')
class WorkflowImageHTTP(unittest.TestCase):
    setUpClass=classmethod(multinode.MultiNodeHTTP.setUpClass.__func__)
    tearDownClass=classmethod(multinode.MultiNodeHTTP.tearDownClass.__func__)
    setUp=multinode.MultiNodeHTTP.setUp
    tearDown=multinode.MultiNodeHTTP.tearDown
    start=multinode.MultiNodeHTTP.start
    advance=multinode.MultiNodeHTTP.advance

    def spec(self,image=IMAGE):
        spec=payload()
        spec['environments']=spec['environments'][:1]
        spec['environments'][0]['image']=image
        spec['jobs']=spec['jobs'][:1]
        return spec

    def test_missing_quay_image_can_prepare_and_bootstrap_uses_immutable_id(self):
        remote=ImageRemote()
        task,space=self.start(self.spec(),remote)
        self.assertEqual(self.advance(task,space)['status'],'SUCCEEDED')
        self.assertEqual(remote.pulls,1)
        self.assertEqual(len(remote.bootstrap_scripts),1)
        self.assertIn(DIGEST,remote.bootstrap_scripts[0])
        self.assertNotIn(IMAGE,remote.bootstrap_scripts[0])

    def test_local_image_never_pulls(self):
        remote=ImageRemote(present=True)
        task,space=self.start(self.spec(),remote)
        self.assertEqual(self.advance(task,space)['status'],'SUCCEEDED')
        self.assertEqual(remote.pulls,0)

    def test_missing_other_repository_fails_without_pull_or_container_creation(self):
        remote=ImageRemote()
        task,space=self.start(self.spec('example/other:missing'),remote)
        self.assertEqual(self.advance(task,space,'FAILED')['status'],'FAILED')
        self.assertEqual(remote.pulls,0)
        self.assertEqual(remote.containers,{})

    def test_pull_failure_stops_environment_without_creating_a_container(self):
        remote=ImageRemote(exit_code=1)
        task,space=self.start(self.spec(),remote)
        self.assertEqual(self.advance(task,space,'FAILED')['status'],'FAILED')
        for _ in range(3):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(remote.pulls,1)
        self.assertEqual(remote.containers,{})
        self.assertEqual(self.client.get('/api/spaces').json()[0]['status'],'CLOSED')

    def test_interrupted_pull_cancellation_keeps_resources_until_deadline(self):
        remote=ImageRemote(interrupt=True)
        task,space=self.start(self.spec(),remote)
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(remote.pulls,1)
        self.client.post('/api/workflows/'+task['id']+'/cancel')
        self.client.post('/api/spaces/'+space['id']+'/close')
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/spaces').json()[0]['status'],'CLOSING')
        self.assertNotIn(self.client.get('/api/requests').json()[0]['status'],{'RELEASED','CANCELLED'})
        with patch('hive.workflow_images.now',return_value=now()+timedelta(seconds=800)):
            self.s.workflows.tick_space(space['id'])
        self.assertEqual(self.client.get('/api/spaces').json()[0]['status'],'CLOSED')
        self.assertEqual(remote.pulls,1)
        self.assertEqual(remote.containers,{})

    def test_cancel_during_pull_does_not_bootstrap_when_pull_returns(self):
        remote=ImageRemote()
        task,space=self.start(self.spec(),remote)
        remote.on_pull=lambda:self.client.post('/api/workflows/'+task['id']+'/cancel')
        self.s.workflows.tick_space(space['id'])
        self.assertEqual(remote.pulls,1)
        self.assertEqual(remote.containers,{})
