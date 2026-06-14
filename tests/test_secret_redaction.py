from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import RepoSnapshot, RunContext, StageContext
from testfabric.core.events import Event
from testfabric.execution.executors.base import ExecResult, ExecutableCommand
from testfabric.execution.executors.local import LocalExecutorAdapter
from testfabric.execution.suites.base import JobPlan, TestItem
from testfabric.execution.suites.command import CommandRunnerAdapter
from testfabric.execution.suites.pytest import PytestRunnerAdapter
from testfabric.infra.live_logger import LiveLogger
from testfabric.orchestrator.dispatch.threaded import LocalDispatcher


class _FakePytestExecutor:
    def run(self, ctx, cmd, *, on_stdout=None, on_stderr=None):
        return ExecResult(
            exit_code=0,
            stdout="tests/test_sample.py::test_ok\n",
            stderr="token=XXXX\n",
        )


class SecretRedactionTests(unittest.TestCase):
    def _build_ctx(self, root: Path) -> tuple[PathManager, RunContext, StageContext]:
        spec = SimpleNamespace(
            run=SimpleNamespace(
                artifacts_dir=str(root / "artifacts"),
                runs_subdir="runs",
                workers_tmp=str(root / "workers_tmp"),
            )
        )
        paths = PathManager(spec, "run-001")
        paths.ensure_run_dirs()
        events = LiveLogger(paths.run_dir, echo=False, redaction_values=("XXXX",))
        run_ctx = RunContext(
            run_id="run-001",
            mode="run",
            paths=paths,
            repo=RepoSnapshot(repo_path=str(root / "repo"), commit_sha="abc123"),
            events=events,
            redaction_values=("XXXX",),
        )
        stage_ref = StageRef(index=1, title="Secrets", suite="smoke")
        ctx = StageContext(
            run=run_ctx,
            suite_name="smoke",
            stage_index=1,
            stage_title="Secrets",
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
        )
        paths.ensure_worker_tmp_dirs(ctx.worker_id)
        paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)
        return paths, run_ctx, ctx

    def test_live_logger_redacts_events_and_streams(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logger = LiveLogger(Path(td), echo=False, redaction_values=("XXXX",))
            logger.emit(Event("demo", "secret", "success", {"token": "XXXX"}))
            logger.stream("logs/out.log", "value=XXXX\n")

            events_text = (Path(td) / "events.jsonl").read_text(encoding="utf-8")
            out_text = (Path(td) / "logs" / "out.log").read_text(encoding="utf-8")

            self.assertIn("***", events_text)
            self.assertIn("***", out_text)
            self.assertNotIn("XXXX", events_text)
            self.assertNotIn("XXXX", out_text)

    def test_dispatcher_redacts_cmd_and_job_logs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            paths, run_ctx, ctx = self._build_ctx(root)

            suite_cfg = SimpleNamespace(
                kind="command",
                steps=[
                    SimpleNamespace(
                        name="secret-step",
                        cmd=["bash", "-lc", "printf 'XXXX\\n'"],
                        workdir=".",
                        env={},
                        continue_on_fail=False,
                        always=False,
                    )
                ],
                dry_steps=[],
                extra_env={},
            )
            runner = CommandRunnerAdapter()
            dispatcher = LocalDispatcher()
            executor = LocalExecutorAdapter()
            job = JobPlan(job_id="job-001", items=[TestItem(id="secret-step")])

            def make_cmd(plan: JobPlan, attempt: int, host_reports_dir: str) -> ExecutableCommand:
                base_cmd = runner.make_job_command(
                    ctx,
                    suite_cfg,
                    plan,
                    attempt=attempt,
                    artifacts_root=ctx.artifacts_root_in_env,
                )
                return ExecutableCommand(
                    cmd=list(base_cmd.cmd),
                    env=dict(base_cmd.env),
                    workdir=base_cmd.workdir,
                    workdir_repo=base_cmd.workdir_repo,
                    artifacts_dir=host_reports_dir,
                    artifacts_root=base_cmd.artifacts_root,
                    network=base_cmd.network,
                    shm_size=base_cmd.shm_size,
                    keep=base_cmd.keep,
                    entrypoint=base_cmd.entrypoint,
                    timeout_seconds=base_cmd.timeout_seconds,
                )

            dispatch = dispatcher.run_jobs(
                ctx=ctx,
                plan=[job],
                make_cmd=make_cmd,
                executor_adapter=executor,
            )

            self.assertEqual(dispatch.failed_job_ids, [])

            attempt_dir = Path(ctx.stage_jobs_dir) / "job-001" / "attempt-0"
            cmd_log = (attempt_dir / "cmd.log").read_text(encoding="utf-8")
            stdout_log = (attempt_dir / "stdout.log").read_text(encoding="utf-8")
            stream_log = (paths.run_dir / "logs" / "stdout.stream.log").read_text(encoding="utf-8")

            self.assertIn("***", cmd_log)
            self.assertIn("***", stdout_log)
            self.assertIn("***", stream_log)
            self.assertNotIn("XXXX", cmd_log)
            self.assertNotIn("XXXX", stdout_log)
            self.assertNotIn("XXXX", stream_log)

    def test_pytest_collect_logs_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            _, _, ctx = self._build_ctx(root)

            runner = PytestRunnerAdapter()
            suite_cfg = SimpleNamespace(
                kind="pytest",
                path="tests",
                rootdir=None,
                markers="",
                args="--token=XXXX -q",
                extra_env={},
                select=[],
            )

            items = runner.collect(ctx, suite_cfg, executor=_FakePytestExecutor())

            self.assertEqual([item.id for item in items], ["./tests/test_sample.py::test_ok"])

            collect_cmd = (Path(ctx.stage_logs_dir) / "collect-cmd.txt").read_text(encoding="utf-8")
            collect_err = (Path(ctx.stage_logs_dir) / "collect-stderr.log").read_text(encoding="utf-8")

            self.assertIn("***", collect_cmd)
            self.assertIn("***", collect_err)
            self.assertNotIn("XXXX", collect_cmd)
            self.assertNotIn("XXXX", collect_err)


if __name__ == "__main__":
    unittest.main()
