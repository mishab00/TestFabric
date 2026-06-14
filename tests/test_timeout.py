from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import textwrap
import unittest

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import RepoSnapshot, RunContext, StageContext
from testfabric.execution.executors.base import ExecutableCommand
from testfabric.execution.executors.local import LocalExecutorAdapter
from testfabric.execution.suites.base import JobPlan, TestItem
from testfabric.orchestrator.dispatch.threaded import LocalDispatcher
from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.spec.schema import RunSpec


class TimeoutTests(unittest.TestCase):
    def test_compile_run_plan_carries_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      name: sample
                      repo_url: git@example.com:org/repo.git
                      ref: main
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    workers:
                      mode: local
                      max_workers: 5

                    pipeline:
                      stages:
                        - title: timeout-stage
                          suite: cmd
                          executor: local
                          timeout:
                            job_seconds: 7
                            stage_seconds: 20

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: run
                            cmd: ["bash", "-lc", "echo hi"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        stage = plan.stages[0]
        self.assertEqual(stage.job_timeout_seconds, 7)
        self.assertEqual(stage.stage_timeout_seconds, 20)

    def test_local_executor_enforces_job_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_stage_context(td)
            cmd = ExecutableCommand(
                cmd=["bash", "-lc", "sleep 2"],
                env={},
                workdir=".",
                workdir_repo=True,
                timeout_seconds=1,
            )

            result = LocalExecutorAdapter().run(ctx, cmd)

        self.assertEqual(result.exit_code, 124)
        self.assertTrue(result.timed_out)
        self.assertIn("TESTFABRIC TIMEOUT", result.stderr)

    def test_dispatcher_marks_pending_jobs_when_stage_timeout_expires(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_stage_context(td, max_workers=1, stage_timeout_seconds=1)
            Path(ctx.stage_jobs_dir).mkdir(parents=True, exist_ok=True)
            Path(ctx.stage_reports_dir).mkdir(parents=True, exist_ok=True)

            jobs = [
                JobPlan(job_id="job-0001", items=[TestItem(id="a")]),
                JobPlan(job_id="job-0002", items=[TestItem(id="b")]),
            ]

            def make_cmd(job: JobPlan, attempt: int, artifacts_root: str) -> ExecutableCommand:
                return ExecutableCommand(
                    cmd=["bash", "-lc", "sleep 2"],
                    env={},
                    workdir=".",
                    workdir_repo=True,
                    artifacts_dir=ctx.stage_reports_dir,
                    artifacts_root=artifacts_root,
                )

            dispatch = LocalDispatcher().run_jobs(
                ctx=ctx,
                plan=jobs,
                make_cmd=make_cmd,
                executor_adapter=LocalExecutorAdapter(),
            )

        self.assertEqual(sorted(dispatch.failed_job_ids), ["job-0001", "job-0002"])
        self.assertEqual(sorted(dispatch.timed_out_job_ids), ["job-0001", "job-0002"])

        by_job = {result.job_id: result for result in dispatch.results}
        self.assertTrue(by_job["job-0001"].started)
        self.assertTrue(by_job["job-0001"].timed_out)
        self.assertFalse(by_job["job-0002"].started)
        self.assertTrue(by_job["job-0002"].timed_out)
        self.assertEqual(by_job["job-0002"].timeout_scope, "stage")

    def _make_stage_context(
        self,
        td: str,
        *,
        max_workers: int = 1,
        stage_timeout_seconds: int | None = None,
    ) -> StageContext:
        root = Path(td)
        repo_dir = root / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        spec = SimpleNamespace(
            run=SimpleNamespace(
                artifacts_dir=str(root / "artifacts"),
                runs_subdir="runs",
                workers_tmp=str(root / "workers_tmp"),
            )
        )
        paths = PathManager(spec, "run-001")
        run_ctx = RunContext(
            run_id="run-001",
            mode="run",
            paths=paths,
            repo=RepoSnapshot(repo_path=str(repo_dir), commit_sha="abc123"),
        )
        stage_ref = StageRef(index=1, title="Timeout", suite="cmd")
        return StageContext(
            run=run_ctx,
            suite_name="cmd",
            stage_index=1,
            stage_title="Timeout",
            stage_slug=stage_ref.slug,
            stage_ref=stage_ref,
            executor="local",
            runner="command",
            kind="command",
            build=False,
            max_workers=max_workers,
            chunk_size=1,
            max_retries=0,
            worker_id="w001",
            stage_timeout_seconds=stage_timeout_seconds,
        )


if __name__ == "__main__":
    unittest.main()
