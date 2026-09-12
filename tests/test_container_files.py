"""Artifact reads tested at the SSH boundary, with no real node access."""
import base64
import hashlib
import unittest
import shutil
import subprocess
from hive.container_runtime import ContainerRuntime
from hive.domain import CommandResult, DomainError
from tests.test_container_runtime import SSH, IDENTITY, NODE


def response(raw, chunk=None):
    return f'HIVE_FILE {len(raw)} {hashlib.sha256(raw).hexdigest()}\n' + base64.b64encode(raw if chunk is None else chunk).decode()


class ContainerFileTests(unittest.TestCase):
    def test_file_read_is_identity_pinned_and_binary_preserving(self):
        raw = b'\x00binary\xff\n'
        ssh = SSH(response(raw))
        self.assertEqual(ContainerRuntime(ssh).read_file(NODE,IDENTITY,'/results/out.bin'),raw)
        script = ssh.calls[0][1]
        self.assertIn(IDENTITY['container_id'],script)
        self.assertIn('test ! -L /results',script)
        self.assertIn('test ! -L /results/out.bin',script)
        self.assertIn('test -f /results/out.bin',script)

    def test_large_file_is_chunked_and_changed_digest_is_rejected(self):
        raw = b'x'*(1024*1024+13)
        ssh = SSH(response(raw,raw[:1024*1024]),response(raw,raw[1024*1024:]))
        self.assertEqual(ContainerRuntime(ssh).read_file(NODE,IDENTITY,'/out.bin'),raw)
        self.assertEqual(len(ssh.calls),2)
        bad = SSH(response(raw,raw[:1024*1024]),response(b'y'*len(raw),b'y'*13))
        with self.assertRaises(DomainError):
            ContainerRuntime(bad).read_file(NODE,IDENTITY,'/out.bin')

    def test_invalid_path_remote_symlink_directory_and_oversize_never_return_data(self):
        for path in ('../file','/a/../file','/a//file','/a/','/a\x00file'):
            ssh = SSH()
            with self.assertRaises(DomainError):
                ContainerRuntime(ssh).read_file(NODE,IDENTITY,path)
            self.assertEqual(ssh.calls,[])
        for output in (CommandResult('','symlink ancestor',1),CommandResult('','directory',1),
                       'HIVE_FILE 4194305 '+'a'*64+'\n'):
            with self.assertRaises(DomainError):
                ContainerRuntime(SSH(output)).read_file(NODE,IDENTITY,'/out.bin')

    @unittest.skipUnless(shutil.which('bash'),'Bash is required for syntax checking')
    def test_file_read_script_has_valid_bash_syntax(self):
        ssh = SSH(response(b'hello'))
        ContainerRuntime(ssh).read_file(NODE,IDENTITY,"/output/some ' file.bin")
        result = subprocess.run([shutil.which('bash'),'-n'],input=ssh.calls[0][1],text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
