from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from testfabric.api.store import SqliteRunStore
from testfabric.core.contracts import RunLease, RunResult, RunStatus
from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec
from testfabric.worker.serve import WorkerServeLoop, WorkerServeSummary


def build_run_result_from_raw(lease: RunLease, raw: dict[str, Any]) -> RunResult:
    ok = bool(raw.get("ok"))
    state = "completed" if ok else "failed"
    verdict = str(raw.get("verdict") or ("PASSED" if ok else "FAILED"))
    message = str(raw.get("error") or "").strip() or None
    return RunResult(
        run_id=lease.run_id,
        ok=ok,
        verdict=verdict,
        state=state,
        run_dir=str(raw.get("run_dir") or "") or None,
        summary_path=str(raw.get("run_summary_path") or "") or None,
        error=message,
        status=RunStatus(
            run_id=lease.run_id,
            state=state,
            verdict=verdict,
            message=message or ("completed" if ok else "failed"),
            progress={"stages": len(list(raw.get("stages") or []))},
            metadata={
                "worker_id": lease.claimed_by,
                "lease_claimed_at": lease.claimed_at,
            },
        ),
        summary=dict(raw),
        metadata={
            "worker_id": lease.claimed_by,
            "lease_claimed_at": lease.claimed_at,
        },
    )


def execute_lease_with_orchestrator(lease: RunLease) -> RunResult:
    spec_payload = lease.request.spec
    if spec_payload is None:
        raise ValueError(f"Run {lease.run_id} does not carry a resolved spec payload")
    spec = RunSpec.model_validate(spec_payload)
    options = RunOptions.from_spec_and_cli(
        spec,
        mode=lease.request.mode,
        run_id=lease.run_id,
        resolved_inputs=dict(lease.request.inputs or {}),
    )
    raw = Orchestrator(spec, options).run()
    return build_run_result_from_raw(lease, raw)


@dataclass(slots=True)
class WorkerRuntime:
    store: SqliteRunStore
    executor: Callable[[RunLease], RunResult] | None = None
    heartbeat_interval_seconds: float = 5.0

    def _default_executor(self, lease: RunLease) -> RunResult:
        return execute_lease_with_orchestrator(lease)

    def run_once(
        self,
        *,
        worker_id: str | None = None,
        worker_labels: list[str] | None = None,
        policy: str = "fifo",
    ) -> RunResult | None:
        lease = self.store.claim_next_run(worker_id=worker_id)
        if lease is None:
            return None
        executor = self.executor or self._default_executor
        stop_event = threading.Event()

        def _pulse() -> None:
            interval = max(0.0, float(self.heartbeat_interval_seconds or 0.0))
            if interval <= 0.0:
                return
            while not stop_event.wait(interval):
                self.store.heartbeat_run(lease.run_id, worker_id=lease.claimed_by)

        heartbeat_thread: threading.Thread | None = None
        if self.heartbeat_interval_seconds and float(self.heartbeat_interval_seconds) > 0:
            self.store.heartbeat_run(lease.run_id, worker_id=lease.claimed_by)
            heartbeat_thread = threading.Thread(target=_pulse, name=f"testfabric-heartbeat-{lease.run_id}", daemon=True)
            heartbeat_thread.start()

        try:
            result = executor(lease)
        finally:
            stop_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2.0)

        self.store.complete_run(lease.run_id, result)
        return result

    def serve(
        self,
        *,
        worker_id: str | None = None,
        worker_labels: list[str] | None = None,
        policy: str = "fifo",
        max_runs: int | None = None,
        max_idle_polls: int | None = None,
        idle_sleep_seconds: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> WorkerServeSummary:
        loop = WorkerServeLoop(self, idle_sleep_seconds=idle_sleep_seconds)
        return loop.serve(
            worker_id=worker_id,
            worker_labels=worker_labels,
            policy=policy,
            max_runs=max_runs,
            max_idle_polls=max_idle_polls,
            stop_event=stop_event,
        )
