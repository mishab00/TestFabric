# testfabric/orchestrator/dispatch/threaded.py
from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Any
from collections import OrderedDict
import time

from testfabric.core.events import Event, utc_ts
from testfabric.core.context import StageContext
from testfabric.artifacts.contract import host_output_dir
from testfabric.execution.suites.base import JobPlan
from testfabric.execution.executors.base import ExecutableCommand
from testfabric.orchestrator.dispatch.models import DispatchJobResult, DispatchResult
from testfabric.watch.runtime import WatchRuntime


class LocalDispatcher:
    """
    Executes jobs on the controller using the stage's executor (docker/local).
    parallelism = ctx.max_workers
    retry per job
    writes per-attempt logs under ctx.stage_jobs_dir
    emits live events + streams stdout/stderr globally
    """

    def run_jobs(
        self,
        *,
        ctx: StageContext,
        plan: list[JobPlan],
        make_cmd: Callable[[JobPlan, int, str], ExecutableCommand],
        executor_adapter: Any,
        watch: dict[str, Any] | None = None,
        output_handler: Callable[[str, dict[str, Any]], object] | None = None,
    ) -> DispatchResult:
        max_workers = int(ctx.max_workers)
        max_retries = int(ctx.max_retries)  # retries AFTER first attempt

        # attempts counter per job_id (attempt 0 is first run)
        attempts: dict[str, int] = {j.job_id: 0 for j in plan}
        pending: list[JobPlan] = list(plan)

        # ✅ track FINAL/LATEST attempt result per job_id
        latest_by_job: dict[str, DispatchJobResult] = OrderedDict()
        timed_out_job_ids: set[str] = set()
        watch_runtime = WatchRuntime(
            watch or {},
            events=ctx.events,
            run_scope={
                "run_id": ctx.run.run_id,
                "stage_id": ctx.stage_slug,
                "target_id": ctx.target_id,
                "worker_id": ctx.worker_id,
            },
            base_dir=Path(ctx.run.run_dir),
            redact_text=ctx.run.redact,
        )
        watch_runtime.start()
        stage_deadline = (
            time.monotonic() + float(ctx.stage_timeout_seconds)
            if ctx.stage_timeout_seconds is not None
            else None
        )

        Path(ctx.stage_jobs_dir).mkdir(parents=True, exist_ok=True)

        ctx.events.emit(
            Event(
                "dispatcher",
                "start",
                "start",
                {
                    "jobs": len(plan),
                    "max_workers": max_workers,
                    "stage_id": ctx.stage_slug,
                    "target_id": ctx.target_id,
                    "transport_type": ctx.transport_type or ctx.executor,
                },
            )
        )

        def remaining_stage_seconds() -> int | None:
            if stage_deadline is None:
                return None
            remaining = stage_deadline - time.monotonic()
            if remaining <= 0:
                return 0
            return max(1, int(remaining))

        def effective_timeout_seconds() -> int | None:
            stage_remaining = remaining_stage_seconds()
            job_timeout = int(ctx.job_timeout_seconds) if ctx.job_timeout_seconds is not None else None
            if stage_remaining is None:
                return job_timeout
            if job_timeout is None:
                return stage_remaining
            return min(job_timeout, stage_remaining)

        def make_stage_timeout_result(job: JobPlan, *, started: bool, attempt: int) -> DispatchJobResult:
            timeout_scope = "stage"
            message = (
                f"Stage timeout exceeded ({ctx.stage_timeout_seconds}s)"
                if ctx.stage_timeout_seconds is not None
                else "Stage timeout exceeded"
            )
            ended_ts = utc_ts()
            ctx.events.emit(
                Event(
                    "job",
                    "end",
                    "fail",
                    {
                        "job_id": job.job_id,
                        "attempt": attempt,
                        "exit_code": 124,
                        "timeout_scope": timeout_scope,
                        "started": started,
                        "error": message,
                        "stage_id": ctx.stage_slug,
                        "target_id": ctx.target_id,
                        "transport_type": ctx.transport_type or ctx.executor,
                    },
                )
            )
            return DispatchJobResult(
                job_id=job.job_id,
                attempt=attempt,
                exit_code=124,
                started=started,
                started_at=None,
                ended_at=ended_ts,
                timed_out=True,
                timeout_scope=timeout_scope,
                error=message,
                items_count=len(job.items),
                item_ids=[item.id for item in job.items],
                duration_seconds=0.0,
            )

        def make_watch_abort_result(job: JobPlan, *, started: bool, attempt: int) -> DispatchJobResult:
            message = watch_runtime.failure_reason or "Watch aborted execution"
            ended_ts = utc_ts()
            ctx.events.emit(
                Event(
                    "job",
                    "end",
                    "fail",
                    {
                        "job_id": job.job_id,
                        "attempt": attempt,
                        "exit_code": 130,
                        "timeout_scope": "watch",
                        "started": started,
                        "error": message,
                        "stage_id": ctx.stage_slug,
                        "target_id": ctx.target_id,
                        "transport_type": ctx.transport_type or ctx.executor,
                    },
                )
            )
            return DispatchJobResult(
                job_id=job.job_id,
                attempt=attempt,
                exit_code=130,
                started=started,
                started_at=None,
                ended_at=ended_ts,
                timed_out=True,
                timeout_scope="watch",
                error=message,
                items_count=len(job.items),
                item_ids=[item.id for item in job.items],
                duration_seconds=0.0,
            )

        def mark_pending_stage_timeout(*, include_job: JobPlan | None = None) -> None:
            jobs = []
            if include_job is not None:
                jobs.append(include_job)
            jobs.extend(pending[:])
            pending.clear()
            for job in jobs:
                if job.job_id in latest_by_job:
                    continue
                attempt = attempts.get(job.job_id, 0)
                jr = make_stage_timeout_result(job, started=False, attempt=attempt)
                latest_by_job[job.job_id] = jr
                timed_out_job_ids.add(job.job_id)

        def mark_pending_watch_abort(*, include_job: JobPlan | None = None) -> None:
            jobs = []
            if include_job is not None:
                jobs.append(include_job)
            jobs.extend(pending[:])
            pending.clear()
            for job in jobs:
                if job.job_id in latest_by_job:
                    continue
                attempt = attempts.get(job.job_id, 0)
                jr = make_watch_abort_result(job, started=False, attempt=attempt)
                latest_by_job[job.job_id] = jr
                timed_out_job_ids.add(job.job_id)

        def run_one(job: JobPlan) -> DispatchJobResult:
            attempt = attempts[job.job_id]
            timeout_seconds = effective_timeout_seconds()

            if timeout_seconds is not None and timeout_seconds <= 0:
                return make_stage_timeout_result(job, started=False, attempt=attempt)

            ctx.events.emit(
                Event(
                    "job",
                    "start",
                    "start",
                    {
                        "job_id": job.job_id,
                        "attempt": attempt,
                        "stage_id": ctx.stage_slug,
                        "target_id": ctx.target_id,
                        "transport_type": ctx.transport_type or ctx.executor,
                    },
                )
            )
            started_at = time.monotonic()
            started_ts = utc_ts()

            attempt_dir = Path(ctx.stage_jobs_dir) / job.job_id / f"attempt-{attempt}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            host_output_dir(ctx.stage_reports_dir, job.job_id, attempt).mkdir(parents=True, exist_ok=True)

            cmd_log = attempt_dir / "cmd.log"
            out_log = attempt_dir / "stdout.log"
            err_log = attempt_dir / "stderr.log"

            cmd = make_cmd(job, attempt, ctx.stage_reports_dir)
            cmd = ExecutableCommand(
                cmd=list(cmd.cmd),
                env=dict(cmd.env or {}),
                workdir=cmd.workdir,
                workdir_repo=bool(cmd.workdir_repo),
                artifacts_dir=cmd.artifacts_dir,
                artifacts_root=cmd.artifacts_root,
                network=cmd.network,
                shm_size=cmd.shm_size,
                keep=bool(cmd.keep),
                entrypoint=cmd.entrypoint,
                timeout_seconds=timeout_seconds,
            )

            # Safety guard: docker MUST have host artifacts mount
            if (ctx.executor or "").strip().lower() == "docker" and not (cmd.artifacts_dir or "").strip():
                raise RuntimeError("BUG: docker job command missing artifacts_dir (host mount path)")

            cmd_log.write_text(ctx.run.redact(" ".join(cmd.cmd) + "\n"), encoding="utf-8")

            try:
                with out_log.open("a", encoding="utf-8") as f_out, err_log.open("a", encoding="utf-8") as f_err:

                    def on_out(t: str) -> object:
                        safe = ctx.run.redact(t)
                        f_out.write(safe)
                        f_out.flush()
                        ctx.events.stream("logs/stdout.stream.log", safe, echo_prefix=f"[{job.job_id}] ")
                        watch_runtime.feed_output(
                            safe,
                            stream="stdout",
                            phase="job",
                            context={"job_id": job.job_id, "attempt": attempt},
                        )
                        if watch_runtime.abort_requested:
                            return False
                        if output_handler is not None:
                            result = output_handler(
                                safe,
                                {
                                    "run_id": ctx.run.run_id,
                                    "job_id": job.job_id,
                                    "attempt": attempt,
                                    "stage_id": ctx.stage_slug,
                                    "target_id": ctx.target_id,
                                    "target_name": ctx.target_name,
                                    "target_group": ctx.target_group,
                                    "target_address": ctx.target_address,
                                    "worker_id": ctx.worker_id,
                                    "stream": "stdout",
                                    "phase": "job",
                                },
                            )
                            if result is False:
                                return False
                            if isinstance(result, str):
                                return result
                        return True

                    def on_err(t: str) -> object:
                        safe = ctx.run.redact(t)
                        f_err.write(safe)
                        f_err.flush()
                        ctx.events.stream("logs/stderr.stream.log", safe, echo_prefix=f"[{job.job_id} !] ")
                        watch_runtime.feed_output(
                            safe,
                            stream="stderr",
                            phase="job",
                            context={"job_id": job.job_id, "attempt": attempt},
                        )
                        if watch_runtime.abort_requested:
                            return False
                        if output_handler is not None:
                            result = output_handler(
                                safe,
                                {
                                    "run_id": ctx.run.run_id,
                                    "job_id": job.job_id,
                                    "attempt": attempt,
                                    "stage_id": ctx.stage_slug,
                                    "target_id": ctx.target_id,
                                    "target_name": ctx.target_name,
                                    "target_group": ctx.target_group,
                                    "target_address": ctx.target_address,
                                    "worker_id": ctx.worker_id,
                                    "stream": "stderr",
                                    "phase": "job",
                                },
                            )
                            if result is False:
                                return False
                            if isinstance(result, str):
                                return result
                        return not watch_runtime.abort_requested

                    r = executor_adapter.run(ctx, cmd, on_stdout=on_out, on_stderr=on_err)

                rc = int(r.exit_code)
                timed_out = bool(getattr(r, "timed_out", False))
                timeout_scope = None
                if timed_out:
                    if ctx.stage_timeout_seconds is not None and (
                        ctx.job_timeout_seconds is None or timeout_seconds < int(ctx.job_timeout_seconds)
                    ):
                        timeout_scope = "stage"
                    else:
                        timeout_scope = "job"
                    timed_out_job_ids.add(job.job_id)
                if watch_runtime.abort_requested and rc == 0:
                    rc = 130
                    timed_out = True
                    timeout_scope = "watch"
                    timed_out_job_ids.add(job.job_id)
                ended_ts = utc_ts()
                ctx.events.emit(
                    Event(
                        "job",
                        "end",
                        "success" if rc == 0 else "fail",
                        {
                            "job_id": job.job_id,
                            "attempt": attempt,
                            "exit_code": rc,
                            "timed_out": timed_out,
                            "timeout_scope": timeout_scope,
                            "stage_id": ctx.stage_slug,
                            "target_id": ctx.target_id,
                            "transport_type": ctx.transport_type or ctx.executor,
                        },
                    )
                )
                return DispatchJobResult(
                    job_id=job.job_id,
                    attempt=attempt,
                    exit_code=rc,
                    started=True,
                    started_at=started_ts,
                    ended_at=ended_ts,
                    timed_out=timed_out,
                    timeout_scope=timeout_scope,
                    items_count=len(job.items),
                    item_ids=[item.id for item in job.items],
                    duration_seconds=max(0.0, time.monotonic() - started_at),
                )

            except Exception as e:
                with err_log.open("a", encoding="utf-8") as f_err:
                    f_err.write(ctx.run.redact(f"\nDISPATCHER ERROR: {e}\n"))

                ended_ts = utc_ts()
                ctx.events.emit(
                    Event(
                        "job",
                        "end",
                        "fail",
                        {
                            "job_id": job.job_id,
                            "attempt": attempt,
                            "exit_code": 99,
                            "error": str(e),
                            "stage_id": ctx.stage_slug,
                            "target_id": ctx.target_id,
                            "transport_type": ctx.transport_type or ctx.executor,
                        },
                    )
                )
                return DispatchJobResult(
                    job_id=job.job_id,
                    attempt=attempt,
                    exit_code=99,
                    started=False,
                    started_at=started_ts,
                    ended_at=ended_ts,
                    error=str(e),
                    items_count=len(job.items),
                    item_ids=[item.id for item in job.items],
                    duration_seconds=max(0.0, time.monotonic() - started_at),
                )

        def submit(pool: ThreadPoolExecutor, futures: dict, job: JobPlan) -> None:
            if watch_runtime.abort_requested:
                mark_pending_watch_abort(include_job=job)
                return
            if remaining_stage_seconds() == 0:
                mark_pending_stage_timeout(include_job=job)
                return
            futures[pool.submit(run_one, job)] = job

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures: dict = {}

                while pending and len(futures) < max_workers:
                    submit(pool, futures, pending.pop(0))

                while futures:
                    for fut in as_completed(list(futures.keys())):
                        job = futures.pop(fut)

                        jr = fut.result()

                        # ✅ overwrite latest result (final status is last attempt)
                        latest_by_job[jr.job_id] = jr
                        if jr.timed_out:
                            timed_out_job_ids.add(jr.job_id)

                        if watch_runtime.abort_requested:
                            mark_pending_watch_abort()
                        # retry?
                        elif jr.exit_code != 0 and not jr.timed_out and jr.attempt < max_retries:
                            attempts[jr.job_id] = jr.attempt + 1
                            ctx.events.emit(
                                Event(
                                    "job",
                                    "retry",
                                    "start",
                                    {
                                        "job_id": jr.job_id,
                                        "next_attempt": attempts[jr.job_id],
                                        "stage_id": ctx.stage_slug,
                                        "target_id": ctx.target_id,
                                        "transport_type": ctx.transport_type or ctx.executor,
                                    },
                                )
                            )
                            submit(pool, futures, job)

                        if remaining_stage_seconds() == 0:
                            mark_pending_stage_timeout()

                        while pending and len(futures) < max_workers:
                            submit(pool, futures, pending.pop(0))
                        break
        finally:
            watch_runtime.stop()
            try:
                watch_runtime.finalize(phase="stage")
            except Exception:
                pass

        # ✅ only final attempt decides failure
        final_results = list(latest_by_job.values())
        failed_ids = sorted([r.job_id for r in final_results if r.exit_code != 0])
        watch_failure_reason = watch_runtime.failure_reason

        ctx.events.emit(
            Event(
                "dispatcher",
                "end",
                "success" if not failed_ids and not watch_failure_reason else "fail",
                {
                    "failed": len(failed_ids),
                    "timed_out": len(timed_out_job_ids),
                    "watch_failure_reason": watch_failure_reason,
                    "stage_id": ctx.stage_slug,
                    "target_id": ctx.target_id,
                    "transport_type": ctx.transport_type or ctx.executor,
                },
            )
        )

        return DispatchResult(
            results=final_results,
            failed_job_ids=failed_ids,
            timed_out_job_ids=sorted(timed_out_job_ids),
            watch_failure_reason=watch_failure_reason,
        )
