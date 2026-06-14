# testfabric/orchestrator/stage/executor.py
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from testfabric.core.context import StageContext
from testfabric.core.events import Event
from testfabric.execution.executors.base import ExecutableCommand
from testfabric.execution.executors.registry import ExecutorRegistry
from testfabric.execution.suites.registry import RunnerRegistry
from testfabric.execution.suites.base import JobPlan
from testfabric.orchestrator.plan.job_planner import Planner
from testfabric.orchestrator.plan.manifest import load_manifest_groups
from testfabric.reporting.stage_reporter import StageReporter
from testfabric.expect.runtime import ExpectRuntime


@dataclass(frozen=True)
class JobResult:
    job_id: str
    attempt: int
    exit_code: int
    timed_out: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    items_total: int
    jobs_total: int
    jobs_finished: int
    jobs_failed: int
    failed_job_ids: list[str]
    timed_out_job_ids: list[str]
    results: list[JobResult]
    reports_dir: str
    watch_failure_reason: str | None = None


class StageExecutor:
    """
    Executes test workload for a stage (no worker lifecycle, no build lifecycle).
    """

    def __init__(
        self,
        *,
        executors: ExecutorRegistry,
        runners: RunnerRegistry,
        planner: Planner,
        dispatcher: Any,
        reporter: StageReporter,
    ) -> None:
        self.executors = executors
        self.runners = runners
        self.planner = planner
        self.dispatcher = dispatcher
        self.reporter = reporter

    def dry_run(self, ctx: StageContext, suite_cfg: Any) -> dict[str, Any]:
        executor = self.executors.get(ctx.executor)
        runner = self.runners.get(ctx.runner)

        items = runner.collect(ctx, suite_cfg, executor=executor)
        split_manifest_groups = self._split_manifest_groups(ctx)
        item_weights = self._item_weights(ctx)
        plan = self.planner.plan(
            items,
            chunk_size=int(ctx.chunk_size),
            execution_mode=ctx.execution_mode,
            execution_count=int(ctx.execution_count),
            split_count=int(ctx.split_count),
            split_manifest_path=ctx.split_manifest_path,
            split_manifest_groups=split_manifest_groups,
            item_weights=item_weights,
        )

        self.reporter.write_collect_summary(ctx, items_total=len(items), jobs_total=len(plan.jobs))

        payload = {
            "run_id": ctx.run.run_id,
            "worker_id": ctx.worker_id,
            "stage": {
                "index": ctx.stage_index,
                "title": ctx.stage_title,
                "suite": ctx.suite_name,
                "kind": ctx.kind,
                "slug": ctx.stage_slug,
            },
            "executor": ctx.executor,
            "runner": ctx.runner,
            "parallelism": {
                "max_workers": int(ctx.max_workers),
                "chunk_size": int(ctx.chunk_size),
                "max_retries": int(ctx.max_retries),
            },
            "execution": {
                "mode": ctx.execution_mode,
                "count": int(ctx.execution_count),
            },
            "split": {
                "count": int(ctx.split_count),
                "manifest_path": ctx.split_manifest_path,
            },
            "timeout": {
                "job_seconds": ctx.job_timeout_seconds,
                "stage_seconds": ctx.stage_timeout_seconds,
            },
            "items_total": plan.items_total,
            "jobs_total": len(plan.jobs),
            "jobs_finished": 0,
            "jobs_failed": 0,
            "failed_job_ids": [],
            "timed_out_job_ids": [],
            "results": [],
            "reports_dir": ctx.stage_reports_dir,
            "ok": True,
            "error": None,
            "mode": "dry-run",
        }

        return payload

    def execute(self, ctx: StageContext, suite_cfg: Any) -> tuple[ExecutionResult, dict[str, Any]]:
        executor = self.executors.get(ctx.executor)
        runner = self.runners.get(ctx.runner)

        items = runner.collect(ctx, suite_cfg, executor)
        split_manifest_groups = self._split_manifest_groups(ctx)
        item_weights = self._item_weights(ctx)
        plan = self.planner.plan(
            items,
            chunk_size=ctx.chunk_size,
            execution_mode=ctx.execution_mode,
            execution_count=int(ctx.execution_count),
            split_count=int(ctx.split_count),
            split_manifest_path=ctx.split_manifest_path,
            split_manifest_groups=split_manifest_groups,
            item_weights=item_weights,
        )

        self.reporter.write_collect_summary(ctx, items_total=len(items), jobs_total=len(plan.jobs))

        host_reports_dir = ctx.stage_reports_dir
        artifacts_root = ctx.artifacts_root_in_env
        expect_runtime = None
        expect_steps = list(getattr(suite_cfg, "expect", []) or [])
        if getattr(suite_cfg, "kind", "") == "expect" or expect_steps:
            if (ctx.executor or "").strip().lower() != "local":
                raise ValueError("interactive command suites currently require executor='local'")
            expect_runtime = ExpectRuntime(
                expect_steps or list(getattr(suite_cfg, "steps", []) or []),
                events=ctx.events,
                run_scope={
                    "run_id": ctx.run.run_id,
                    "stage_id": ctx.stage_slug,
                    "target_id": ctx.target_id,
                    "worker_id": ctx.worker_id,
                },
                redact_text=ctx.run.redact,
            )
            if len(plan.jobs) != 1:
                raise ValueError("interactive command suites currently require exactly one planned job")

        def make_cmd(job: JobPlan, attempt: int, host_reports_dir_arg: str) -> ExecutableCommand:
            base_cmd = runner.make_job_command(ctx, suite_cfg, job, attempt=attempt, artifacts_root=artifacts_root)
            return self._with_artifacts_dir(base_cmd, host_reports_dir_arg)

        dispatch = self.dispatcher.run_jobs(
            ctx=ctx,
            plan=plan.jobs,
            make_cmd=make_cmd,
            executor_adapter=executor,
            watch=ctx.watch,
            output_handler=expect_runtime.feed_output if expect_runtime else None,
        )

        self.reporter.write_dispatch_summary(ctx, dispatch)

        exec_payload = self.reporter.write_stage_summary(
            ctx,
            items_total=plan.items_total,
            jobs_total=len(plan.jobs),
            dispatch=dispatch,
        )

        exec_res = ExecutionResult(
            items_total=int(exec_payload["items_total"]),
            jobs_total=int(exec_payload["jobs_total"]),
            jobs_finished=int(exec_payload["jobs_finished"]),
            jobs_failed=int(exec_payload["jobs_failed"]),
            failed_job_ids=[
                str(r.get("job_id") or "")
                for r in list(exec_payload.get("jobs") or exec_payload.get("results") or [])
                if str(r.get("verdict") or "").upper() in {"FAILED", "TIMED_OUT"}
                and str(r.get("job_id") or "").strip()
            ],
            timed_out_job_ids=[
                str(r.get("job_id") or "")
                for r in list(exec_payload.get("jobs") or exec_payload.get("results") or [])
                if bool(r.get("timed_out", False)) and str(r.get("job_id") or "").strip()
            ],
            results=[
                JobResult(
                    job_id=r["job_id"],
                    attempt=int(r["attempt"]),
                    exit_code=int(r.get("exit_code") or 0),
                    timed_out=bool(r.get("timed_out", False)),
                )
                for r in list(exec_payload.get("jobs") or exec_payload.get("results") or [])
            ],
            reports_dir=str(
                (exec_payload.get("debug") or {}).get("stage_reports_dir")
                or exec_payload.get("reports_dir")
                or ctx.stage_reports_dir
            ),
            watch_failure_reason=str(
                exec_payload.get("watch_failure_reason")
                or ((exec_payload.get("watch") or {}).get("failure_reason") if isinstance(exec_payload.get("watch"), dict) else "")
                or ""
            ).strip()
            or None,
        )
        return exec_res, exec_payload

    def _with_artifacts_dir(self, cmd: ExecutableCommand, host_reports_dir: str) -> ExecutableCommand:
        return ExecutableCommand(
            cmd=list(cmd.cmd),
            env=dict(cmd.env or {}),
            workdir=cmd.workdir,
            workdir_repo=bool(cmd.workdir_repo),
            artifacts_dir=str(host_reports_dir),
            artifacts_root=str(cmd.artifacts_root or ""),
            network=cmd.network,
            shm_size=cmd.shm_size,
            keep=bool(getattr(cmd, "keep", False)),
            entrypoint=cmd.entrypoint,
            timeout_seconds=cmd.timeout_seconds,
        )

    def _split_manifest_groups(self, ctx: StageContext) -> list[list[str]] | None:
        if not ctx.split_manifest_path:
            return None
        return load_manifest_groups(
            ctx,
            manifest_path=ctx.split_manifest_path,
        )

    def _item_weights(self, ctx: StageContext) -> dict[str, float]:
        return {}
