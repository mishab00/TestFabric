from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import OutputsConfig, RepoSnapshot, RunContext, StageContext
from testfabric.orchestrator.aggregate import RunAggregator
from testfabric.orchestrator.dispatch.models import DispatchJobResult, DispatchResult
from testfabric.orchestrator.stage.types import StageResult
from testfabric.reporting.stage_reporter import StageReporter


class StageSummaryArtifactTests(unittest.TestCase):
    def _build_ctx(self, root: Path) -> tuple[PathManager, StageContext]:
        spec = SimpleNamespace(
            run=SimpleNamespace(
                artifacts_dir=str(root / "artifacts"),
                runs_subdir="runs",
                workers_tmp=str(root / "workers_tmp"),
                repo_url=".",
                ref="HEAD",
                name="artifact-test",
            )
        )
        paths = PathManager(spec, "run-001")
        run_ctx = RunContext(
            run_id="run-001",
            mode="run",
            paths=paths,
            repo=RepoSnapshot(repo_path=str(root / "repo"), commit_sha="abc123"),
        )
        stage_ref = StageRef(index=1, title="Evidence", suite="smoke")
        ctx = StageContext(
            run=run_ctx,
            suite_name="smoke",
            stage_index=1,
            stage_title="Evidence",
            stage_slug=stage_ref.slug,
            stage_ref=stage_ref,
            executor="local",
            runner="command",
            kind="command",
            build=False,
            max_workers=1,
            chunk_size=1,
            max_retries=0,
            worker_id="w001",
            outputs=OutputsConfig(junit="junit.xml", html="report.html"),
        )
        paths.ensure_run_dirs()
        paths.ensure_worker_tmp_dirs(ctx.worker_id)
        paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)
        return paths, ctx

    def test_stage_summary_inlines_artifact_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, ctx = self._build_ctx(root)

            stage_root = Path(ctx.stage_tmp_dir)
            (stage_root / "logs" / "collect-stdout.log").write_text("collected\n", encoding="utf-8")
            (stage_root / "jobs" / "job-001" / "attempt-0").mkdir(parents=True, exist_ok=True)
            (stage_root / "jobs" / "job-001" / "attempt-0" / "stdout.log").write_text("out\n", encoding="utf-8")
            (stage_root / "jobs" / "job-001" / "attempt-0" / "stderr.log").write_text("err\n", encoding="utf-8")
            (stage_root / "reports" / "junit-job-001-a0.xml").write_text("<xml />\n", encoding="utf-8")
            (stage_root / "reports" / "report-job-001-a0.html").write_text("<html></html>\n", encoding="utf-8")
            (stage_root / "reports" / "custom.txt").write_text("artifact\n", encoding="utf-8")
            artifact_dir = stage_root / "reports" / "artifacts" / "jobs" / "job-001" / "attempt-0"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "screenshot.png").write_text("png\n", encoding="utf-8")

            dispatch = DispatchResult(
                results=[DispatchJobResult(job_id="job-001", attempt=0, exit_code=0)],
                failed_job_ids=[],
                timed_out_job_ids=[],
            )

            payload = StageReporter().write_stage_summary(
                ctx,
                items_total=1,
                jobs_total=1,
                dispatch=dispatch,
            )
            payload["duration_seconds"] = 0.5
            StageReporter().ensure_stage_summary(
                ctx,
                ok=True,
                error=None,
                exec_payload=payload,
                duration_seconds=0.5,
                failure_type=None,
            )

            summary_path = stage_root / "stage-summary.json"
            self.assertTrue(summary_path.exists())

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            artifacts = summary["artifacts"]
            self.assertNotIn("reports_path", artifacts)
            self.assertEqual(artifacts["counts_by_category"]["log"], 3)
            self.assertEqual(artifacts["counts_by_category"]["junit"], 1)
            self.assertEqual(artifacts["counts_by_category"]["html"], 1)
            self.assertEqual(artifacts["counts_by_category"]["artifact"], 2)
            self.assertTrue(any(path.endswith("screenshot.png") for path in artifacts["primary"]))
            self.assertTrue(any(path.endswith(".xml") for path in artifacts["primary"]))
            self.assertTrue(any(item["category"] == "junit" for item in artifacts["highlights"]))
            self.assertTrue(any(item["label"] == "JUnit XML" for item in artifacts["highlights"]))
            self.assertEqual(summary["debug"]["stage_reports_path"], "workers/w001/stages/01-evidence/reports")
            self.assertNotIn("stage_reports_dir", summary["debug"])

    def test_run_summary_surfaces_stage_artifacts_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths, ctx = self._build_ctx(root)

            stage_root = Path(ctx.stage_tmp_dir)
            (stage_root / "reports" / "custom.txt").write_text("artifact\n", encoding="utf-8")

            reporter = StageReporter()
            reporter.ensure_stage_summary(
                ctx,
                ok=True,
                error=None,
                exec_payload=None,
                duration_seconds=0.1,
                failure_type=None,
            )

            summary_rel = f"workers/{ctx.worker_id}/stages/{ctx.stage_slug}/stage-summary.json"
            summary_abs = str((paths.run_dir / summary_rel).resolve())
            mirror_root = paths.mirror_worker_dir(ctx.worker_id) / "stages" / ctx.stage_slug
            mirror_root.mkdir(parents=True, exist_ok=True)
            (mirror_root / "stage-summary.json").write_text(
                (stage_root / "stage-summary.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            stage_result = StageResult(
                ok=True,
                stage_index=ctx.stage_index,
                stage_title=ctx.stage_title,
                stage_slug=ctx.stage_slug,
                suite=ctx.suite_name,
                kind=ctx.kind,
                executor=ctx.executor,
                runner=ctx.runner,
                run_id=ctx.run.run_id,
                run_dir=str(paths.run_dir),
                worker_id=ctx.worker_id,
                worker_tmp_dir=str(paths.worker_tmp_dir(ctx.worker_id)),
                worker_mirror_dir=str(paths.mirror_worker_dir(ctx.worker_id)),
                stage_summary_path=summary_abs,
                stage_summary_rel=summary_rel,
            )

            RunAggregator().write_run_summary(
                spec=SimpleNamespace(run=SimpleNamespace(repo_url=".", ref="HEAD", name="artifact-test")),
                paths=paths,
                run_id=ctx.run.run_id,
                ok=True,
                stage_results=[stage_result],
            )

            run_summary = json.loads(paths.run_summary_path.read_text(encoding="utf-8"))
            stage_payload = run_summary["stages"][0]
            self.assertNotIn("reports_path", stage_payload["artifacts"])
            self.assertEqual(stage_payload["artifacts"]["files_total"], 1)
            self.assertEqual(stage_payload["debug"]["stage_reports_path"], f"workers/{ctx.worker_id}/stages/{ctx.stage_slug}/reports")
            self.assertNotIn("stage_reports_dir", stage_payload["debug"])
            run_artifacts = run_summary["artifacts"]
            self.assertEqual(run_artifacts["files_total"], 1)
            self.assertEqual(run_artifacts["counts_by_category"]["artifact"], 1)
            self.assertEqual(run_artifacts["counts_by_source"]["reports"], 1)
            self.assertEqual(run_artifacts["primary"][0]["stage_id"], ctx.stage_slug)
            self.assertEqual(run_artifacts["primary"][0]["stage_title"], ctx.stage_title)
            self.assertEqual(run_artifacts["primary"][0]["target_name"], "local")
            self.assertEqual(run_artifacts["primary"][0]["category"], "artifact")
            self.assertEqual(run_artifacts["primary"][0]["source"], "reports")
            self.assertEqual(run_artifacts["primary"][0]["label"], "Artifact: custom.txt")


if __name__ == "__main__":
    unittest.main()
