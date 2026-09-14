"""The public preset exposes scripts and preserves native command failures."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ARCHIVE = Path(__file__).resolve().parents[1]
PREFIX = "hive_presets/qwen3-30b-a3b-w8a8/"


class TaskScriptsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "Bash is needed for the native shell contract")
    def test_native_benchmark_failure_survives_foreground_log_tee(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deps").mkdir()
            (root / "deps/activate.sh").write_text(":\n", encoding="utf-8")
            (root / "output/client").mkdir(parents=True)
            (root / "output/client/benchmark.sh").write_text('printf "native failed\\n"\nexit 7\n', encoding="utf-8")
            environment = dict(
                os.environ,
                HIVE_SOURCE_DIR=root.as_posix(),
                HIVE_TASK_ID="unit-task",
                HIVE_CLIENT_DEPS=(root / "deps").as_posix(),
                HIVE_TASK_OUTPUT_DIR=(root / "output").as_posix(),
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'cd() { if [ "$1" = /var/tmp ]; then return 0; fi; builtin cd "$@"; }; '
                    'export -f cd; exec bash "$1"',
                    "_",
                    (ARCHIVE / "aisbench.sh").as_posix(),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual((root / "output/client/aisbench.log").read_text(), "native failed\n")

    def test_every_execution_stage_exposes_a_named_script_and_complete_outputs(self):
        workflow = json.loads((ARCHIVE / "workflow.json").read_text(encoding="utf-8"))
        source = json.loads((ARCHIVE / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(workflow["retain_minutes"], 4320)
        self.assertEqual(source["input_files"][source["nightly_yaml"]], "case.yaml")
        steps = []
        for environment in workflow["environments"]:
            steps.extend([environment["bootstrap"], *environment["install"], *environment["verify"]])
        for job in workflow["jobs"]:
            steps.extend(step for phase in ("pre", "steps", "ready", "post") for step in job[phase])
            for artifact in job["artifacts"]:
                self.assertNotIn("${task_id}", artifact["path"])
                self.assertNotIn("${job_id}", artifact["path"])
                if artifact["path"].startswith("/var/tmp/hive-nightly/"):
                    self.assertIn("${HIVE_TASK_ID}", artifact["path"])
        for step in steps:
            names = [entry["name"] for entry in step["files"]]
            script = names[0]
            self.assertTrue(script.startswith(PREFIX), script)
            self.assertTrue(script.endswith(".sh"), script)
            self.assertEqual(step["launch"], 'bash "$HIVE_SOURCE_DIR/' + script + '"')
            for name in names:
                if name.startswith(PREFIX):
                    self.assertIn(name[len(PREFIX) :], source["helper_files"])
                    self.assertTrue((ARCHIVE / name[len(PREFIX) :]).is_file())
        benchmark = next(job for job in workflow["jobs"] if job["id"] == "aisbench")
        self.assertTrue(
            any(item["path"].endswith("/client/results") and item["kind"] == "auto" for item in benchmark["artifacts"])
        )


if __name__ == "__main__":
    unittest.main()
