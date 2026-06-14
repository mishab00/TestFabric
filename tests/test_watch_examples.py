from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import json
import subprocess
from unittest.mock import patch

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


class _FakeHTTPResponse:
    def __init__(self, *, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body


class WatchExampleSpecsTests(unittest.TestCase):
    def test_checked_in_local_command_watch_example_triggers_watcher_and_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_local.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            with patch("testfabric.watch.runtime.subprocess.run") as script_run:
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, run_id="watch-local-command"),
                ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            script_run.assert_called_once()
            self.assertEqual(script_run.call_args.args[0][0], "sh")
            self.assertIn("custom watch action", " ".join(script_run.call_args.args[0]))
            env = script_run.call_args.kwargs.get("env", {})
            self.assertIn("TESTFABRIC_RUN_ID", env)
            self.assertIn("TESTFABRIC_STAGE_ID", env)
            self.assertIn("TESTFABRIC_JOB_ID", env)
            self.assertIn("TESTFABRIC_ATTEMPT", env)
            self.assertIn("TESTFABRIC_TARGET_ID", env)
            self.assertIn("TESTFABRIC_WORKER_ID", env)
            self.assertIn("TESTFABRIC_WATCH_MATCH", env)

    def test_checked_in_remote_docker_watch_example_triggers_fail_watcher(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec, _ = RunSpec.load_resolved(
            str(repo_root / "run_watch_remote_docker.yaml"),
            inputs_map={
                "remote_docker_key_path": "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/id_ed25519",
                "remote_docker_known_hosts": "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/known_hosts",
            },
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            with patch("testfabric.health.probes_docker.DockerHealthProbeSuite.probe") as probe:
                probe.return_value = []
                with patch("testfabric.execution.executors.docker.docker_run") as docker_run:
                    def fake_docker_run(*args, **kwargs):
                        on_stdout = kwargs.get("on_stdout")
                        if on_stdout is not None:
                            on_stdout("WATCH-HIT from remote docker\n")
                        return type(
                            "RunResult",
                            (),
                            {"exit_code": 0, "stdout": "WATCH-HIT from remote docker\n", "stderr": "", "container_id": "c1"},
                        )()

                    docker_run.side_effect = fake_docker_run
                    result = Orchestrator(
                        spec,
                        RunOptions.from_spec_and_cli(
                            spec,
                            run_id="watch-remote-docker",
                            resolved_inputs={
                                "remote_docker_key_path": "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/id_ed25519",
                                "remote_docker_known_hosts": "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/known_hosts",
                            },
                        ),
                    ).run()

            self.assertFalse(result["ok"])
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "FAILED")
            self.assertEqual((summary.get("run") or {}).get("failure", {}).get("stage_title"), "watch remote docker command")

    def test_checked_in_http_watch_example_reports_on_degraded_health(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec, _ = RunSpec.load_resolved(
            str(repo_root / "run_watch_http.yaml"),
            inputs_map={
                "http_watch_port": "8080",
            },
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            with patch("testfabric.watch.runtime.urllib.request.urlopen") as urlopen:
                calls = {"count": 0}

                def fake_urlopen(*args, **kwargs):
                    calls["count"] += 1
                    if calls["count"] < 2:
                        return _FakeHTTPResponse(status=500, body=b"service degraded\n")
                    return _FakeHTTPResponse(status=200, body=b"ok\n")

                urlopen.side_effect = fake_urlopen
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, run_id="watch-http-health"),
                ).run()
            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("stage") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            self.assertEqual((summary.get("run") or {}).get("failure"), None)
            self.assertEqual((summary.get("events") or {}).get("counts_by_action", {}).get("watch:api_degraded"), 1)
            self.assertEqual((summary.get("events") or {}).get("counts_by_component", {}).get("watch"), 1)

    def test_checked_in_file_watch_example_reports_on_artifact_file(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_file.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="watch-file-artifact"),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "file_contains_watch_hit")
            self.assertEqual(summary["watch"]["counts_by_source"]["run_stdout_log"], 1)

    def test_checked_in_sequence_watch_example_reports_on_ordered_messages(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_sequence.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="watch-sequence-order"),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            self.assertEqual(summary["watch"]["counts_by_watcher"]["job_flow_complete"], 1)
            self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "job_flow_complete")

    def test_checked_in_sequence_watch_missing_example_fails_when_final_milestone_is_missing(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_sequence_missing.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="watch-sequence-missing"),
            ).run()

            self.assertFalse(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "FAILED")
            self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "job_flow_complete")
            self.assertEqual(summary["watch"]["counts_by_watcher"]["job_flow_complete"], 1)

    def test_checked_in_remote_file_watch_example_reports_on_remote_log(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_remote_file.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            calls = {"size": 0, "read": 0}

            def fake_run(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                joined = " ".join(str(part) for part in cmd)
                if "wc -c" in joined:
                    calls["size"] += 1
                    if calls["size"] < 2:
                        return subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr="")
                    return subprocess.CompletedProcess(cmd, 0, stdout="24\n", stderr="")
                if "cat " in joined or "tail -c" in joined:
                    calls["read"] += 1
                    return subprocess.CompletedProcess(cmd, 0, stdout="WATCH-HIT from remote file\n", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            with patch("testfabric.watch.runtime.subprocess.run", side_effect=fake_run):
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, run_id="watch-remote-file"),
                ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "remote_file_contains_watch_hit")
            self.assertEqual(summary["watch"]["counts_by_source"]["remote_app_log"], 1)

    def test_checked_in_remote_metric_watch_example_reports_on_remote_host_metric(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_remote_metric.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            def fake_run(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                joined = " ".join(str(part) for part in cmd)
                if "ssh" in joined:
                    return subprocess.CompletedProcess(cmd, 0, stdout="92.5\n", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("testfabric.watch.runtime.subprocess.run", side_effect=fake_run):
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, run_id="watch-remote-metric"),
                ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "remote_metric_over_90")
            self.assertEqual(summary["watch"]["counts_by_source"]["remote_cpu_metric"], 1)

    def test_checked_in_metric_watch_example_reports_on_thresholds(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_metric.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="watch-metric-thresholds"),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 2)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            self.assertEqual(summary["watch"]["counts_by_watcher"]["cpu_over_90"], 1)
            self.assertEqual(summary["watch"]["counts_by_watcher"]["disk_over_90"], 1)
            self.assertEqual(summary["watch"]["counts_by_source"]["cpu_usage"], 1)
            self.assertEqual(summary["watch"]["counts_by_source"]["disk_usage"], 1)

    def test_checked_in_teardown_watch_example_runs_cleanup_action(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_teardown.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            with patch("testfabric.watch.runtime.subprocess.run") as script_run:
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, run_id="watch-teardown-cleanup"),
                ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")
            script_run.assert_called_once()
            env = script_run.call_args.kwargs.get("env", {})
            self.assertIn("TESTFABRIC_RUN_ID", env)
            self.assertIn("TESTFABRIC_STAGE_ID", env)
            self.assertIn("TESTFABRIC_JOB_ID", env)
            self.assertIn("TESTFABRIC_ATTEMPT", env)
            self.assertIn("TESTFABRIC_TARGET_ID", env)
            self.assertIn("TESTFABRIC_WORKER_ID", env)
            self.assertIn("TESTFABRIC_WATCH_MATCH", env)
            self.assertIn("teardown cleanup", script_run.call_args.args[0][-1])
            self.assertIn("docker rm -f", script_run.call_args.args[0][-1])

    def test_checked_in_stderr_watch_example_fails_on_error_output(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_watch_stderr.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="watch-stderr-fail"),
            ).run()

            self.assertFalse(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "FAILED")
            self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "fail_on_stderr")
            self.assertEqual(summary["watch"]["counts_by_source"]["command_err"], 1)

    def test_checked_in_interactive_command_example_sends_prompt_response_and_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_command_interactive_local.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="interactive-command-session"),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("expect") or 0), 1)
            self.assertEqual(summary["run"]["verdict"], "PASSED")


if __name__ == "__main__":
    unittest.main()
