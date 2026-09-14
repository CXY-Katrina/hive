"""The public preset exposes scripts and preserves native command failures."""

import json
import os
import re
import shutil
import subprocess
import sys
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
            (root / "deps/venv/bin").mkdir(parents=True)
            executable = root / "deps/venv/bin/python3"
            executable.write_text(
                '#!/usr/bin/env bash\nprintf "native failed\\n"\nexit 7\n', encoding="utf-8", newline="\n"
            )
            executable.chmod(0o700)
            (root / "cann.sh").write_text(":\n", encoding="utf-8")
            (root / "output/client").mkdir(parents=True)
            (root / "output/client/manifest.json").write_text('{"argv":["ais_bench","config.py"]}', encoding="utf-8")
            deps = (root / "deps").as_posix()
            if os.name == "nt":
                deps = "/" + deps[0].lower() + deps[2:]
            environment = dict(
                os.environ,
                HIVE_SOURCE_DIR=root.as_posix(),
                HIVE_TASK_ID="unit-task",
                TASK_CLIENT_DEPS=deps,
                TASK_OUTPUT_DIR=(root / "output").as_posix(),
                TASK_CANN_ENV=(root / "cann.sh").as_posix(),
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'cd() { if [ "$1" = /var/tmp ]; then return 0; fi; builtin cd "$@"; }; '
                    'export -f cd; exec bash "$1" bench',
                    "_",
                    (ARCHIVE / "task.sh").as_posix(),
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
        self.assertEqual(workflow["retain_minutes"], 0)
        self.assertEqual([job["id"] for job in workflow["jobs"]], ["job1", "job2", "job3"])
        self.assertEqual([job["name"] for job in workflow["jobs"]], ["job1", "job2", "job3"])
        self.assertEqual(sorted(path.name for path in ARCHIVE.glob("*.sh")), ["task.sh"])
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
            self.assertEqual(script, PREFIX + "task.sh")
            self.assertTrue(step["launch"].startswith('bash "$HIVE_SOURCE_DIR/' + script + '" '))
            for name in names:
                if name.startswith(PREFIX):
                    self.assertIn(name[len(PREFIX) :], source["helper_files"])
                    self.assertTrue((ARCHIVE / name[len(PREFIX) :]).is_file())
        benchmark = next(job for job in workflow["jobs"] if job["id"] == "job2")
        self.assertTrue(
            any(item["path"].endswith("/client/results") and item["kind"] == "auto" for item in benchmark["artifacts"])
        )
        script = (ARCHIVE / "task.sh").read_text(encoding="utf-8")
        self.assertEqual(
            set(re.findall(r"\bHIVE_[A-Z0-9_]+", script)), {"HIVE_SOURCE_DIR", "HIVE_TASK_ID", "HIVE_NODE0_IP"}
        )
        self.assertNotIn("activate.sh", script)
        self.assertNotRegex(script, r"(source|bash)\s+.*(?:server|benchmark|verify)\.sh")
        self.assertIn('git -C "$HIVE_SOURCE_DIR" rev-parse HEAD', script)
        self.assertIn("/.github/vllm-main-verified.commit", script)
        self.assertIn('hive_resource model "$TASK_MODEL_NAME"', script)
        self.assertIn('hive_resource dataset "$TASK_DATASET_NAME"', script)

    @unittest.skipUnless(shutil.which("bash"), "Bash is needed for the native shell contract")
    def test_bootstrap_uses_explicit_image_and_selected_container_without_runtime_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap = root / "bootstrap.sh"
            bootstrap.write_text('printf "%s\\n" "$@"\n', encoding="utf-8", newline="\n")
            result = subprocess.run(
                ["bash", (ARCHIVE / "task.sh").as_posix(), "bootstrap", "image:explicit", "hive-client-node2-test"],
                env={**os.environ, "HIVE_SOURCE_DIR": root.as_posix(), "TASK_BOOTSTRAP_SCRIPT": bootstrap.as_posix()},
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["image:explicit", "hive-client-node2-test"])

    @unittest.skipUnless(shutil.which("bash"), "Bash is needed for the native shell contract")
    def test_native_cli_receives_manifest_argv_neutral_cwd_and_propagates_main_return_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("source", "neutral", "image-runtime", "deps/venv/bin", "output/client"):
                (root / name).mkdir(parents=True)
            benchmark = root / "deps/benchmark"
            package = benchmark
            for name in ("ais_bench", "benchmark", "cli"):
                package /= name
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "main.py").write_text(
                "import json,os,sys\ndef main():\n"
                '    print(json.dumps({"argv":sys.argv,"cwd":os.getcwd(),'
                '"paths":os.environ["TASK_OBSERVED_PYTHONPATH"]}))\n    return 7\n',
                encoding="utf-8",
            )
            executable = root / "deps/venv/bin/python3"
            executable.write_text(
                '#!/usr/bin/env bash\nexport TASK_OBSERVED_PYTHONPATH="$PYTHONPATH"\n'
                f'export PYTHONPATH="{benchmark.as_posix()}"\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
                encoding="utf-8",
                newline="\n",
            )
            executable.chmod(0o700)
            (root / "cann.sh").write_text(":\n", encoding="utf-8")
            argv = [
                "ais_bench",
                str(root / "output/client/benchmark.py"),
                "--mode",
                "perf",
                "--num-prompts",
                "180",
                "--work-dir",
                str(root / "output/client/results"),
                "--debug",
            ]
            (root / "output/client/manifest.json").write_text(json.dumps({"argv": argv}), encoding="utf-8")

            def shell_path(path):
                value = path.as_posix()
                return "/" + value[0].lower() + value[2:] if os.name == "nt" else value

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'cd() { if [ "$1" = /var/tmp ]; then builtin cd "$TASK_TEST_NEUTRAL"; '
                    'else builtin cd "$@"; fi; }; export -f cd; exec bash "$1" bench',
                    "_",
                    (ARCHIVE / "task.sh").as_posix(),
                ],
                env={
                    **os.environ,
                    "HIVE_SOURCE_DIR": shell_path(root / "source"),
                    "HIVE_TASK_ID": "task-native-cli",
                    "TASK_CLIENT_DEPS": shell_path(root / "deps"),
                    "TASK_OUTPUT_DIR": (root / "output").as_posix(),
                    "TASK_CANN_ENV": (root / "cann.sh").as_posix(),
                    "TASK_TEST_NEUTRAL": (root / "neutral").as_posix(),
                    "PYTHONPATH": shell_path(root / "source") + ":" + shell_path(root / "image-runtime"),
                },
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            actual = json.loads((root / "output/client/aisbench.log").read_text())
            self.assertEqual(actual["argv"], argv)
            self.assertEqual(Path(actual["cwd"]).resolve(), (root / "neutral").resolve())
            observed_paths = [Path(value).resolve() for value in actual["paths"].split(os.pathsep)]
            self.assertIn((root / "image-runtime").resolve(), observed_paths)
            self.assertNotIn((root / "source").resolve(), observed_paths)


if __name__ == "__main__":
    unittest.main()
