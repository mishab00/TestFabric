# testfabric/orchestrator/orchestrator.py
from __future__ import annotations

from dataclasses import replace
import uuid
import json
from pathlib import Path
from typing import Literal, Any

from pydantic import BaseModel, Field

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.artifacts.store import ArtifactStore

from testfabric.core.context import RunContext
from testfabric.core.events import Event
from testfabric.infra.live_logger import LiveLogger

from testfabric.workspace.controller import ControllerWorkspace

from testfabric.execution.executors.registry import ExecutorRegistry
from testfabric.execution.suites.registry import RunnerRegistry

from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.orchestrator.aggregate import RunAggregator

from testfabric.workers.pool import WorkerPool
from testfabric.reporting.stage_reporter import StageReporter
from testfabric.orchestrator.stage.builder import StageBuilder
from testfabric.orchestrator.stage.executor import StageExecutor
from testfabric.orchestrator.stage.coordinator import StageCoordinator
from testfabric.orchestrator.stage.types import StageResult
from testfabric.orchestrator.dispatch.threaded import LocalDispatcher
from testfabric.orchestrator.plan.job_planner import Planner
from testfabric.orchestrator.plan.lifecycle import LifecycleState, after_stage, before_stage, should_execute_stage
from testfabric.health.policy import HealthPolicy, evaluate_health
from testfabric.health.probes_docker import DockerHealthProbeSuite
from testfabric.health.probes_local import LocalHealthProbeSuite
from testfabric.spec.schema import RunSpec


Mode = Literal["run", "dry-run"]


class RunOptions(BaseModel):
    mode: Mode = "run"
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    resolved_inputs: dict[str, Any] = Field(default_factory=dict)
    console_verbosity: Literal["quiet", "summary", "normal", "verbose"] = "normal"

    @classmethod
    def from_spec_and_cli(
        cls,
        spec: RunSpec,
        *,
        mode: Mode = "run",
        run_id: str | None = None,
        resolved_inputs: dict[str, Any] | None = None,
        console_verbosity: Literal["quiet", "summary", "normal", "verbose"] | None = None,
    ) -> "RunOptions":
        rid = (run_id or getattr(getattr(spec, "run", None), "run_id", None) or "").strip()
        if not rid:
            base = (getattr(getattr(spec, "run", None), "name", None) or "adhoc").strip() or "adhoc"
            rid = f"{base}-{uuid.uuid4().hex[:10]}"
        verbosity = console_verbosity or getattr(getattr(spec, "run", None), "console_verbosity", None) or "normal"
        return cls(
            mode=mode,
            run_id=rid,
            resolved_inputs=dict(resolved_inputs or {}),
            console_verbosity=verbosity,
        )


class Orchestrator:
    def __init__(self, spec: RunSpec, options: RunOptions):
        self.spec = spec
        self.options = options
        self.paths = PathManager(spec, options.run_id)

    def _secret_redaction_values(self) -> tuple[str, ...]:
        values: list[str] = []
        for name, definition in dict(getattr(self.spec, "inputs", {}) or {}).items():
            if getattr(definition, "type", None) != "secret":
                continue
            if name not in self.options.resolved_inputs:
                continue
            text = str(self.options.resolved_inputs[name] or "")
            if text:
                values.append(text)
        return tuple(sorted(set(values), key=len, reverse=True))

    def run(self, *, suite_override: str | None = None, build: bool = False) -> dict[str, Any]:
        self.paths.ensure_run_dirs()
        redaction_values = self._secret_redaction_values()
        live = LiveLogger(
            run_dir=self.paths.run_dir,
            echo=True,
            console_verbosity=self.options.console_verbosity,
            redaction_values=redaction_values,
            run_id=self.options.run_id,
        )

        run_ctx: RunContext | None = None
        stage_results: list[object] = []
        overall_ok = True
        err: str | None = None
        health_snapshot: dict[str, Any] | None = None

        try:
            # Checkout once on controller
            ws = ControllerWorkspace(self.spec)
            snap = ws.ensure_checked_out()

            run_ctx = RunContext(
                run_id=self.options.run_id,
                mode=self.options.mode,
                paths=self.paths,
                repo=snap,
                events=live,
                env={},
                resolved_inputs=dict(self.options.resolved_inputs or {}),
                redaction_values=redaction_values,
            )

            run_ctx.events.emit(
                Event(
                    "run",
                    "start",
                    "start",
                    {"run_id": run_ctx.run_id, "repo_path": snap.repo_path, "sha": snap.commit_sha},
                )
            )

            plan = compile_run_plan(
                self.spec,
                run_id=self.options.run_id,
                mode=self.options.mode,
                suite_override=suite_override,
                build=bool(build),  # NEW
            )
            run_ctx = replace(run_ctx, watch=dict(getattr(plan, "watch", {}) or {}))

            requires_docker = any(
                (getattr(st, "executor", "") or "").strip().lower() == "docker"
                and not bool(getattr(st, "docker_endpoint", {}) or {})
                for st in plan.stages
            )
            health_policy = HealthPolicy(
                require_docker=bool(getattr(self.spec.health, "require_docker", False)) or requires_docker,
                min_disk_gb=int(getattr(self.spec.health, "min_disk_gb", 1)),
                min_mem_gb=getattr(self.spec.health, "min_mem_gb", None),
            )
            health_checks = []
            health_checks.extend(
                LocalHealthProbeSuite(health_policy).probe(
                    writable_paths=[
                        self.paths.artifacts_root,
                        self.paths.workers_tmp_root,
                        self.paths.run_dir,
                        Path(snap.repo_path),
                    ]
                )
            )
            docker_endpoints: list[dict[str, Any]] = []
            seen_endpoints: set[str] = set()
            for st in plan.stages:
                endpoint = dict(getattr(st, "docker_endpoint", {}) or {})
                if not endpoint:
                    continue
                key = json.dumps(endpoint, sort_keys=True, default=str)
                if key in seen_endpoints:
                    continue
                seen_endpoints.add(key)
                docker_endpoints.append(endpoint)

            health_checks.extend(
                DockerHealthProbeSuite().probe(
                    required=health_policy.require_docker,
                    endpoints=docker_endpoints,
                )
            )
            snapshot = evaluate_health(health_checks)
            health_snapshot = snapshot.as_dict()

            run_ctx.events.emit(
                Event(
                    "health",
                    "preflight",
                    "fail" if snapshot.state == "unhealthy" else "success",
                    health_snapshot,
                )
            )
            if snapshot.state == "unhealthy":
                reasons = "; ".join(snapshot.reasons or ["Health preflight failed"])
                raise RuntimeError(f"Worker health preflight failed: {reasons}")

            executors = ExecutorRegistry()
            runners = RunnerRegistry()
            planner = Planner()
            artifacts = ArtifactStore(self.paths)

            pool = WorkerPool(
                mode=self.spec.workers.mode,
                capacity=int(self.spec.effective_worker_capacity()),
                health_state=snapshot.state,
                health_reasons=list(snapshot.reasons or []),
            )
            reporter = StageReporter()
            dispatcher = LocalDispatcher()

            builder = StageBuilder(executors=executors)
            stage_exec = StageExecutor(
                executors=executors,
                runners=runners,
                planner=planner,
                dispatcher=dispatcher,
                reporter=reporter,
            )

            coordinator = StageCoordinator(
                spec=self.spec,
                paths=self.paths,
                artifacts=artifacts,
                pool=pool,
                builder=builder,
                executor=stage_exec,
                reporter=reporter,
            )

            lifecycle_state = LifecycleState()
            for st in plan.stages:
                lifecycle_state = before_stage(lifecycle_state, st)
                should_run, skip_reason = should_execute_stage(st, state=lifecycle_state)
                if not should_run:
                    if run_ctx is not None:
                        run_ctx.events.emit(
                            Event(
                                "stage",
                                "skip",
                                "skip",
                                {
                                    "index": st.index,
                                    "title": st.title,
                                    "suite": st.suite_name,
                                    "stage_id": StageRef(index=st.index, title=st.title, suite=st.suite_name).slug,
                                    "target_id": st.target_id,
                                    "target_name": st.target_name,
                                    "transport_type": st.transport_type,
                                    "reason": skip_reason,
                                },
                            )
                        )
                    stage_results.append(
                        StageResult(
                            ok=False,
                            stage_index=st.index,
                            stage_title=st.title,
                            stage_slug=StageRef(index=st.index, title=st.title, suite=st.suite_name).slug,
                            suite=st.suite_name,
                            kind=st.kind,
                            executor=st.executor,
                            runner=st.runner,
                            run_id=st.run_id,
                            run_dir=str(self.paths.run_dir),
                            worker_id="",
                            worker_tmp_dir="",
                            worker_mirror_dir="",
                            target_id=st.target_id,
                            target_name=st.target_name,
                            target_kind=st.target_kind,
                            target_group=st.target_group,
                            target_address=st.target_address,
                            target_labels=dict(st.target_labels or {}),
                            transport_type=st.transport_type,
                            error=skip_reason,
                            failure_type="skipped",
                            skipped=True,
                            skip_reason=skip_reason,
                            lifecycle_mode=st.lifecycle_mode,
                            lifecycle_when=st.lifecycle_when,
                            started_at=None,
                            ended_at=None,
                        )
                    )
                    continue

                r = coordinator.run(run_ctx=run_ctx, stage=st)
                stage_results.append(r)
                if not getattr(r, "ok", False):
                    overall_ok = False
                lifecycle_state = after_stage(lifecycle_state, st, ok=bool(getattr(r, "ok", False)))

            return {
                "ok": overall_ok,
                "run_id": self.options.run_id,
                "run_dir": str(self.paths.run_dir),
                "run_summary_path": str(self.paths.run_summary_path),
                "health": health_snapshot,
                "stages": [getattr(r, "__dict__", {}) for r in stage_results],
            }

        except Exception as e:
            overall_ok = False
            err = str(e)
            return {
                "ok": False,
                "run_id": self.options.run_id,
                "run_dir": str(self.paths.run_dir),
                "run_summary_path": str(self.paths.run_summary_path),
                "health": health_snapshot,
                "stages": [getattr(r, "__dict__", {}) for r in stage_results],
                "error": err,
            }

        finally:
            try:
                if run_ctx is not None:
                    run_ctx.events.emit(
                        Event(
                            "run",
                            "end",
                            "success" if overall_ok else "fail",
                            {"run_id": run_ctx.run_id, "error": err},
                        )
                    )
            except Exception:
                pass

            try:
                RunAggregator().write_run_summary(
                    spec=self.spec,
                    paths=self.paths,
                    run_id=self.options.run_id,
                    ok=overall_ok,
                    stage_results=stage_results,
                    error=err,
                    health=health_snapshot,
                )
            except Exception:
                pass
