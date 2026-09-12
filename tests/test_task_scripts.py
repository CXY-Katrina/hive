"""Real local Linux protocol checks; explicitly skipped on Windows/macOS.

These run harmless jobs only in a fresh temporary task directory, without SSH,
MySQL, NPU hardware, containers, or node installation.
"""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "backend" / "hive" / "scripts"
LINUX_READY = (sys.platform.startswith("linux") and
               all(shutil.which(tool) for tool in ("bash", "flock", "setsid", "timeout", "nohup", "ps")))


@unittest.skipUnless(LINUX_READY, "Requires real Linux procfs, procps, flock, setsid and GNU timeout")
class LinuxTaskProtocol(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="hive_protocol_"))
        shutil.copyfile(SCRIPT_DIR / "task_runner.sh", self.directory / "runner.sh")

    def control(self, action):
        completed = subprocess.run(["bash", str(SCRIPT_DIR / "task_control.sh"), str(self.directory), action],
                                   capture_output=True, text=True, timeout=15, check=True)
        return completed.stdout.strip()

    def prepare(self, script, timeout=5):
        (self.directory / "job.sh").write_text(script, encoding="utf-8")
        (self.directory / "timeout").write_text(str(timeout), encoding="ascii")

    def wait_result(self, limit=20):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            state = self.control("status")
            if state.startswith("EXITED "):
                return int(state.split()[1])
            time.sleep(0.1)
        self.fail(f"No task result; state={state}; path={self.directory}")

    def tearDown(self):
        for _ in range(10):
            if self.control("close") == "CLOSED":
                shutil.rmtree(self.directory)
                return
            time.sleep(0.1)
        # Keep recovery files when closure cannot be proven.
        self.fail(f"Task closure not confirmed; preserved {self.directory}")

    def test_duplicate_launch_detaches_and_runs_once(self):
        count = shlex.quote(str(self.directory / "count"))
        self.prepare(f"echo one >> {count}\nsleep 0.5\nexit 7\n")
        self.assertEqual(self.control("launch"), "STARTING")
        self.assertEqual(self.control("launch"), "EXISTING")
        self.assertEqual(self.wait_result(), 7)
        self.assertEqual((self.directory / "count").read_text().splitlines(), ["one"])

    def test_close_tombstone_blocks_delayed_launch(self):
        self.prepare("echo should-not-run\n")
        self.assertEqual(self.control("close"), "CLOSED")
        self.assertEqual(self.control("launch"), "CLOSED")
        self.assertFalse((self.directory / "identity").exists())
        self.assertFalse((self.directory / "output.log").exists())

    def test_timeout_covers_background_stdout_and_term_ignoring_child(self):
        self.prepare("(trap '' TERM; while :; do sleep 1; done) &\nexit 0\n", timeout=1)
        self.control("launch")
        self.assertIn(self.wait_result(), {124, 137})

    def test_log_cap_drains_without_limiting_user_artifacts(self):
        checkpoint = shlex.quote(str(self.directory / "checkpoint"))
        self.prepare(f"head -c 22000000 /dev/zero > {checkpoint}\nhead -c 22000000 /dev/zero\n", timeout=15)
        self.control("launch")
        self.assertEqual(self.wait_result(), 0)
        self.assertEqual((self.directory / "output.log").stat().st_size, 20971520)
        self.assertEqual((self.directory / "checkpoint").stat().st_size, 22000000)


if __name__ == "__main__":
    unittest.main()
