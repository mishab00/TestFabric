# testfabric/orchestrator/stage/coordinator.py
from __future__ import annotations

from typing import Any
import time

from testfabric.artifacts.paths import StageRef, PathManager
from testfabric.artifacts.store import ArtifactStore
from testfabric.core.context import RunContext, StageContext, OutputsConfig, DockerRuntime
from testfabric.core.events import Event, utc_ts
from testfabric.orchestrator.stage.types import StageResult
from testfabric.orchestrator.stage.builder import StageBuilder
from testfabric.orchestrator.stage.executor import StageExecutor
from testfabric.reporting.stage_reporter import StageReporter
from testfabric.workers.pool import WorkerPool
from testfabric.workers.models import Worker
from testfabric.orchestrator.plan.models import StagePlan
from testfabric.spec.schema import RunSpec
from testfabric.execution.executors.docker_template import resolve_runtime as resolve_docker_runtime


class StageCoordinator:
    """
    Orchestrates ONE stage:
      - acquire worker
      - prepare dirs
      - optional build
      - execute or dry-run
      - ensure stage summary exists
      - mirror artifacts
      - release worker
    """

    def __init__(
        self,
        *,
        spec: RunSpec,
        paths: PathManager,
        artifacts: ArtifactStore,
        pool: WorkerPool,
        builder: StageBuilder,
        executor: StageExecutor,
        reporter: StageReporter,
    ) -> None:
        self.spec = spec
        self.paths = paths
        self.artifacts = artifacts
        self.pool = pool
        self.builder = builder
        self.executor = executor
        self.reporter = reporter

    def run(self, *, run_ctx: RunContext, stage: StagePlan) -> StageResult:
        """
        IMPORTANT: run_ctx is created once by Orchestrator and reused for all stages.
        That’s how we get consistent live logging + streaming.
        """
        worker: Worker | None = None
        worker_id = "w000"

        stage_ref = StageRef(index=stage.index, title=stage.title, suite=stage.suite_name)
        stage_slug = stage_ref.slug

        ok = False
        err: str | None = None
        failure_type: str | None = None
        exec_payload: dict[str, Any] | None = None

        tmp_dir = ""
        mirror_dir = ""
        summary_rel = ""
        summary_abs = ""

        outputs = OutputsConfig(
            junit=getattr(getattr(self.spec, "outputs", None), "junit", None),
            html=getattr(getattr(self.spec, "outputs", None), "html", None),
        )

        docker_rt: DockerRuntime | None = None
        if (stage.executor or "").strip().lower() == "docker":
            docker = getattr(self.spec, "docker", None)
            run = getattr(docker, "run", None) if docker else None
            raw_docker_rt = DockerRuntime(
                mode=str(getattr(docker, "mode", "auto")),
                image=str(getattr(docker, "image", "")),
                dockerfile=str(getattr(docker, "dockerfile", "Dockerfile")),
                build_args=dict(getattr(docker, "build_args", {}) or {}),
                no_cache=bool(getattr(docker, "no_cache", False)),
                base_image=str(getattr(docker, "base_image", "python:3.11-slim")),
                python_requirements=list(getattr(docker, "python_requirements", []) or []),
                pip_packages=list(getattr(docker, "pip_packages", []) or []),
                system_packages=list(getattr(docker, "system_packages", []) or []),
                repo_mount=str(getattr(docker, "repo_mount", "/work")),
                workdir=str(getattr(docker, "workdir", "/work")),
                network=getattr(run, "network", "host") if run else "host",
                shm_size=getattr(run, "shm_size", "1g") if run else "1g",
                env=dict(getattr(run, "env", {}) or {}) if run else {},
                keep_container=bool(getattr(run, "keep_container", False)) if run else False,
            )
            docker_rt = resolve_docker_runtime(run_ctx.repo.repo_path, raw_docker_rt)

        ctx: StageContext | None = None

        # stage start event (controller-level)
        run_ctx.events.emit(
            Event(
                "stage",
                "start",
                "start",
                {
                    "index": stage.index,
                    "title": stage.title,
                    "suite": stage.suite_name,
                    "executor": stage.executor,
                    "stage_id": stage_slug,
                    "target_id": stage.target_id,
                    "target_name": stage.target_name,
                    "transport_type": stage.transport_type,
                },
            )
        )
        stage_started_at = time.monotonic()
        stage_started_ts = utc_ts()

        def derive_failure_type_from_payload(payload: dict[str, Any] | None) -> str | None:
            if not payload:
                return None
            raw = str(payload.get("failure_type") or "").strip()
            if raw:
                return raw
            jobs = list(payload.get("jobs") or [])
            kinds = [str(job.get("failure_type") or "").strip() for job in jobs if str(job.get("failure_type") or "").strip()]
            if not kinds:
                return None
            priority = {"infra": 0, "timeout": 1, "runner": 2, "execution": 3}
            return sorted(kinds, key=lambda item: priority.get(item, 99))[0]

        try:
            worker = self.pool.acquire()
            worker_id = worker.id

            tmp_dir = str(self.paths.worker_tmp_dir(worker_id))
            mirror_dir = str(self.paths.mirror_worker_dir(worker_id))

            # NOTE: keep consistent with ArtifactStore.mirror_stage()
            summary_rel = f"workers/{worker_id}/stages/{stage_slug}/stage-summary.json"
            summary_abs = str((self.paths.run_dir / summary_rel).resolve())

            ctx = StageContext(
                run=run_ctx,
                suite_name=stage.suite_name,
                stage_index=stage.index,
                stage_title=stage.title,
                stage_slug=stage_slug,
                stage_ref=stage_ref,
                executor=stage.executor,
                runner=stage.runner,
                kind=stage.kind,
                build=bool(stage.build),
                execution_mode=stage.execution_mode,
                execution_count=stage.execution_count,
                split_count=stage.split_count,
                split_manifest_path=stage.split_manifest_path,
                job_timeout_seconds=stage.job_timeout_seconds,
                stage_timeout_seconds=stage.stage_timeout_seconds,
                lifecycle_mode=stage.lifecycle_mode,
                lifecycle_when=stage.lifecycle_when,
                max_workers=stage.max_workers,
                chunk_size=stage.chunk_size,
                max_retries=stage.max_retries,
                worker_id=worker_id,
                target_id=stage.target_id,
                target_name=stage.target_name,
                target_kind=stage.target_kind,
                target_group=stage.target_group,
                target_address=stage.target_address,
                target_labels=dict(stage.target_labels or {}),
                target_credential=stage.target_credential,
                docker_transport=dict(stage.docker_transport or {}),
                docker_endpoint=dict(stage.docker_endpoint or {}),
                transport_type=stage.transport_type,
                watch=dict(run_ctx.watch or {}),
                outputs=outputs,
                docker=docker_rt,
                stage_env=dict(stage.env or {}),  # if you kept section

            )

            # ensure stage dirs exist (reporter owns this)
            self.reporter.ensure_dirs(ctx)

            # stage acquired worker event
            ctx.events.emit(
                Event(
                    "stage",
                    "worker",
                    "success",
                    {
                        "worker_id": worker_id,
                        "tmp_dir": tmp_dir,
                        "stage_id": ctx.stage_slug,
                        "target_id": ctx.target_id,
                        "target_name": ctx.target_name,
                        "transport_type": ctx.transport_type,
                    },
                )
            )
            ctx.events.emit(
                Event(
                    "stage",
                    "target",
                    "success",
                    {
                        "worker_id": worker_id,
                        "target_id": ctx.target_id,
                        "target_name": ctx.target_name,
                        "target_kind": ctx.target_kind,
                        "target_group": ctx.target_group,
                        "target_address": ctx.target_address,
                        "stage_id": ctx.stage_slug,
                        "transport_type": ctx.transport_type,
                    },
                )
            )

            # optional build
            self.builder.build_if_needed(ctx)

            # resolve suite config at runtime
            suite_cfg = self.spec.suites[stage.suite_name]

            # execute
            if stage.mode == "dry-run":
                ctx.events.emit(
                    Event(
                        "stage",
                        "execute",
                        "start",
                        {
                            "mode": "dry-run",
                            "stage_id": ctx.stage_slug,
                            "target_id": ctx.target_id,
                            "transport_type": ctx.transport_type,
                        },
                    )
                )
                exec_payload = self.executor.dry_run(ctx, suite_cfg)
                ok = True
                ctx.events.emit(
                    Event(
                        "stage",
                        "execute",
                        "success",
                        {
                            "mode": "dry-run",
                            "stage_id": ctx.stage_slug,
                            "target_id": ctx.target_id,
                            "transport_type": ctx.transport_type,
                        },
                    )
                )
            else:
                ctx.events.emit(
                    Event(
                        "stage",
                        "execute",
                        "start",
                        {
                            "mode": "run",
                            "stage_id": ctx.stage_slug,
                            "target_id": ctx.target_id,
                            "transport_type": ctx.transport_type,
                        },
                    )
                )
                exec_res, exec_payload = self.executor.execute(ctx, suite_cfg)
                if str(getattr(exec_res, "watch_failure_reason", "") or "").strip():
                    failure_type = "watch"
                    raise RuntimeError(str(exec_res.watch_failure_reason).strip())
                ok = exec_res.jobs_failed == 0
                if not ok:
                    if exec_res.timed_out_job_ids:
                        failure_type = "timeout"
                        raise TimeoutError(
                            f"Stage timed out: {len(exec_res.timed_out_job_ids)} job(s) exceeded timeout"
                        )
                    failure_type = derive_failure_type_from_payload(exec_payload) or "execution"
                    raise RuntimeError(f"Stage failed: {exec_res.jobs_failed} job(s) failed")
                ctx.events.emit(
                    Event(
                        "stage",
                        "execute",
                        "success",
                        {
                            "jobs_total": exec_res.jobs_total,
                            "jobs_failed": exec_res.jobs_failed,
                            "stage_id": ctx.stage_slug,
                            "target_id": ctx.target_id,
                            "transport_type": ctx.transport_type,
                        },
                    )
                )

        except Exception as e:
            ok = False
            err = str(e)
            if failure_type is None:
                failure_type = "timeout" if isinstance(e, TimeoutError) else "execution"
            run_ctx.events.emit(
                Event(
                    "stage",
                    "error",
                    "fail",
                    {
                        "index": stage.index,
                        "title": stage.title,
                        "suite": stage.suite_name,
                        "error": err,
                        "failure_type": failure_type,
                        "stage_id": stage_slug,
                        "target_id": stage.target_id,
                        "transport_type": stage.transport_type,
                    },
                )
            )

        finally:
            stage_duration_seconds = max(0.0, time.monotonic() - stage_started_at)
            stage_ended_ts = utc_ts()
            # 1) guarantee stage summary (best-effort)
            if ctx is not None:
                try:
                    self.reporter.ensure_stage_summary(
                        ctx,
                        ok=ok,
                        error=err,
                        exec_payload=exec_payload,
                        duration_seconds=stage_duration_seconds,
                        failure_type=failure_type,
                        started_at=stage_started_ts,
                        ended_at=stage_ended_ts,
                    )
                except Exception:
                    pass

            # 2) mirror artifacts (best-effort) - requires worker_id + stage_ref
            try:
                if worker is not None:
                    self.artifacts.mirror_stage(worker_id, stage_ref)
            except Exception:
                pass

            # 3) always release worker if acquired
            try:
                if worker is not None:
                    self.pool.release(worker)
            except Exception:
                pass

            run_ctx.events.emit(
                Event(
                    "stage",
                    "end",
                    "success" if ok else "fail",
                    {
                        "index": stage.index,
                        "title": stage.title,
                        "suite": stage.suite_name,
                        "worker_id": worker_id,
                        "target_id": stage.target_id,
                        "target_name": stage.target_name,
                        "stage_id": stage_slug,
                        "transport_type": stage.transport_type,
                        "stage_summary": summary_rel or None,
                        "error": err,
                    },
                )
            )

        return StageResult(
            ok=bool(ok),
            stage_index=stage.index,
            stage_title=stage.title,
            stage_slug=stage_slug,
            suite=stage.suite_name,
            kind=stage.kind,
            executor=stage.executor,
            runner=stage.runner,
            run_id=stage.run_id,
            run_dir=str(self.paths.run_dir),
            worker_id=worker_id,
            worker_tmp_dir=tmp_dir,
            worker_mirror_dir=mirror_dir,
            target_id=stage.target_id,
            target_name=stage.target_name,
            target_kind=stage.target_kind,
            target_group=stage.target_group,
            target_address=stage.target_address,
            target_labels=dict(stage.target_labels or {}),
            transport_type=stage.transport_type,
            stage_summary_path=summary_abs or None,
            stage_summary_rel=summary_rel or None,
            repo_path=run_ctx.repo.repo_path,
            commit_sha=run_ctx.repo.commit_sha,
            error=err,
            failure_type=failure_type,
            lifecycle_mode=stage.lifecycle_mode,
            lifecycle_when=stage.lifecycle_when,
            started_at=stage_started_ts,
            ended_at=stage_ended_ts,
            duration_seconds=stage_duration_seconds,
        )
