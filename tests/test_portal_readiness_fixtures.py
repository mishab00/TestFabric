from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


class PortalReadinessFixtureTests(unittest.TestCase):
    def _run_spec(self, spec_path: Path, run_id: str, *, patch_subprocess: bool = False):
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(spec_path))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            if patch_subprocess:
                with patch("testfabric.watch.runtime.subprocess.run") as subprocess_run:
                    result = Orchestrator(
                        spec,
                        RunOptions.from_spec_and_cli(spec, run_id=run_id),
                    ).run()
            else:
                subprocess_run = None
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, run_id=run_id),
                ).run()

            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            stage_summaries = []
            run_dir = Path(result["run_dir"])
            for stage_summary_path in sorted(run_dir.glob("workers/*/stages/*/stage-summary.json")):
                stage_summaries.append(json.loads(stage_summary_path.read_text(encoding="utf-8")))
            return result, summary, stage_summaries, subprocess_run

    def test_portal_simple_success_fixture_passes_and_persists_artifacts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result, summary, stage_summaries, _ = self._run_spec(
            repo_root / "run_portal_simple_success.yaml",
            "portal-simple-success",
        )

        self.assertTrue(result["ok"], msg=result.get("error"))
        self.assertEqual(summary["run"]["verdict"], "PASSED")
        self.assertEqual(len(stage_summaries), 2)
        self.assertTrue(all(stage["verdict"] == "PASSED" for stage in stage_summaries))
        self.assertGreaterEqual(int(summary["totals"].get("artifacts_total") or 0), 1)

    def test_portal_late_failure_fixture_fails_after_passed_stages(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result, summary, stage_summaries, _ = self._run_spec(
            repo_root / "run_portal_late_failure.yaml",
            "portal-late-failure",
        )

        self.assertFalse(result["ok"], msg=result.get("error"))
        self.assertEqual(summary["run"]["verdict"], "FAILED")
        self.assertEqual(len(stage_summaries), 3)
        self.assertEqual(stage_summaries[0]["verdict"], "PASSED")
        self.assertEqual(stage_summaries[1]["verdict"], "PASSED")
        self.assertEqual(stage_summaries[2]["verdict"], "FAILED")
        self.assertEqual(summary["run"]["failure"]["stage_title"], "fail late in run")

    def test_portal_watcher_heavy_fixture_triggers_session_file_and_metric_watchers(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result, summary, _, _ = self._run_spec(
            repo_root / "run_portal_watcher_heavy.yaml",
            "portal-watcher-heavy",
        )

        self.assertTrue(result["ok"], msg=result.get("error"))
        self.assertEqual(summary["run"]["verdict"], "PASSED")
        self.assertEqual(summary["watch"]["counts_by_watcher"]["session_contains_watch_hit"], 1)
        self.assertEqual(summary["watch"]["counts_by_watcher"]["file_contains_watch_hit"], 1)
        self.assertEqual(summary["watch"]["counts_by_watcher"]["cpu_over_90"], 1)
        self.assertGreaterEqual(int(summary["events"]["counts_by_component"].get("watch") or 0), 3)

    def test_portal_retry_fixture_retries_once_then_passes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        marker = Path("/tmp") / "testfabric-portal-retry-portal-retry"
        marker.unlink(missing_ok=True)
        result, summary, stage_summaries, _ = self._run_spec(
            repo_root / "run_portal_retry.yaml",
            "portal-retry",
        )

        self.assertTrue(result["ok"], msg=result.get("error"))
        self.assertEqual(summary["run"]["verdict"], "PASSED")
        self.assertEqual(len(stage_summaries), 1)
        self.assertEqual(stage_summaries[0]["verdict"], "PASSED")
        self.assertEqual(stage_summaries[0]["jobs"][0]["attempt"], 1)
        self.assertEqual(summary["events"]["counts_by_action"].get("job:retry"), 1)

    def test_portal_remote_performance_fixture_reports_remote_metric(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            joined = " ".join(str(part) for part in cmd)
            if "ssh" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="92.5\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("testfabric.watch.runtime.subprocess.run", side_effect=fake_run) as subprocess_run:
            result, summary, _, _ = self._run_spec(
                repo_root / "run_portal_remote_performance.yaml",
                "portal-remote-performance",
            )

        self.assertTrue(result["ok"], msg=result.get("error"))
        self.assertEqual(summary["run"]["verdict"], "PASSED")
        self.assertEqual(summary["watch"]["counts_by_source"]["remote_cpu_metric"], 1)
        self.assertEqual(summary["watch"]["first_trigger"]["watcher"], "remote_metric_over_90")
        subprocess_run.assert_called()
        self.assertTrue(any("ssh" in " ".join(str(part) for part in call.args[0]) for call in subprocess_run.call_args_list))


if __name__ == "__main__":
    unittest.main()
