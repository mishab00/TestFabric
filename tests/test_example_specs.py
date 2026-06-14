from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


class ExampleSpecsTests(unittest.TestCase):
    def _tree(self, root: Path) -> set[str]:
        return {
            str(path.relative_to(root)).replace("\\", "/") or "."
            for path in sorted(root.rglob("*"))
        } | {"."}

    def test_checked_in_minimal_artifact_example_persists_artifacts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_minimal_artifact_local.yaml"))
        root = repo_root / "artifacts" / "test-runs" / "example-minimal-artifact"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)

        spec.run.repo_url = str(repo_root)
        spec.run.workdir = str(root / "work")
        spec.run.artifacts_dir = str(root / "artifacts")

        result = Orchestrator(
            spec,
            RunOptions.from_spec_and_cli(spec, run_id="example-artifact"),
        ).run()

        self.assertTrue(result["ok"], msg=result.get("error"))

        run_dir = Path(result["run_dir"])
        stage_root = run_dir / "workers" / "w000" / "stages" / "01-write_artifact"

        headers_file = stage_root / "reports" / "result.txt"
        summary_file = run_dir / "summary.json"
        stage_summary_file = stage_root / "stage-summary.json"

        self.assertTrue(summary_file.exists())
        self.assertTrue(stage_summary_file.exists())
        self.assertTrue(headers_file.exists())
        self.assertEqual(headers_file.read_text(encoding="utf-8"), "artifact ok\n")
        stage_summary = json.loads(stage_summary_file.read_text(encoding="utf-8"))
        self.assertEqual(stage_summary["verdict"], "PASSED")
        self.assertGreaterEqual(int((stage_summary.get("artifacts") or {}).get("files_total") or 0), 1)

    def test_minimal_artifact_example_creates_only_expected_layout(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_minimal_artifact_local.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="example-layout"),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))

            run_dir = Path(result["run_dir"])
            worker_tmp_dir = Path(spec.run.artifacts_dir) / "workers_tmp" / "example-layout" / "w000"

            self.assertEqual(
                self._tree(run_dir),
                {
                    ".",
                    "events.jsonl",
                    "logs",
                    "logs/stdout.stream.log",
                    "summary.json",
                    "workers",
                    "workers/w000",
                    "workers/w000/stages",
                    "workers/w000/stages/01-write_artifact",
                    "workers/w000/stages/01-write_artifact/collect-summary.json",
                    "workers/w000/stages/01-write_artifact/dispatch-summary.json",
                    "workers/w000/stages/01-write_artifact/jobs",
                    "workers/w000/stages/01-write_artifact/jobs/job-0001",
                    "workers/w000/stages/01-write_artifact/jobs/job-0001/attempt-0",
                    "workers/w000/stages/01-write_artifact/jobs/job-0001/attempt-0/cmd.log",
                    "workers/w000/stages/01-write_artifact/jobs/job-0001/attempt-0/stderr.log",
                    "workers/w000/stages/01-write_artifact/jobs/job-0001/attempt-0/stdout.log",
                    "workers/w000/stages/01-write_artifact/logs",
                    "workers/w000/stages/01-write_artifact/reports",
                    "workers/w000/stages/01-write_artifact/reports/artifacts",
                    "workers/w000/stages/01-write_artifact/reports/artifacts/jobs",
                    "workers/w000/stages/01-write_artifact/reports/artifacts/jobs/job-0001",
                    "workers/w000/stages/01-write_artifact/reports/artifacts/jobs/job-0001/attempt-0",
                    "workers/w000/stages/01-write_artifact/reports/result.txt",
                    "workers/w000/stages/01-write_artifact/stage-summary.json",
                },
            )

            self.assertEqual(
                self._tree(worker_tmp_dir),
                {
                    ".",
                    "stages",
                    "stages/01-write_artifact",
                    "stages/01-write_artifact/collect-summary.json",
                    "stages/01-write_artifact/dispatch-summary.json",
                    "stages/01-write_artifact/jobs",
                    "stages/01-write_artifact/jobs/job-0001",
                    "stages/01-write_artifact/jobs/job-0001/attempt-0",
                    "stages/01-write_artifact/jobs/job-0001/attempt-0/cmd.log",
                    "stages/01-write_artifact/jobs/job-0001/attempt-0/stderr.log",
                    "stages/01-write_artifact/jobs/job-0001/attempt-0/stdout.log",
                    "stages/01-write_artifact/logs",
                    "stages/01-write_artifact/reports",
                    "stages/01-write_artifact/reports/artifacts",
                    "stages/01-write_artifact/reports/artifacts/jobs",
                    "stages/01-write_artifact/reports/artifacts/jobs/job-0001",
                    "stages/01-write_artifact/reports/artifacts/jobs/job-0001/attempt-0",
                    "stages/01-write_artifact/reports/result.txt",
                    "stages/01-write_artifact/stage-summary.json",
                },
            )

    def test_checked_in_stress_example_persists_artifacts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_stress_local.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id="example-stress"),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))

            run_dir = Path(result["run_dir"])
            stage_root = run_dir / "workers" / "w000" / "stages" / "01-repeat-shell-command-locally"

            result_file = stage_root / "reports" / "result.txt"
            summary_file = run_dir / "summary.json"
            stage_summary_file = stage_root / "stage-summary.json"

            self.assertTrue(summary_file.exists())
            self.assertTrue(stage_summary_file.exists())
            self.assertTrue(result_file.exists())
            self.assertEqual(result_file.read_text(encoding="utf-8").strip(), "stress command completed")
            stage_summary = json.loads(stage_summary_file.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int((stage_summary.get("artifacts") or {}).get("files_total") or 0), 1)


if __name__ == "__main__":
    unittest.main()
