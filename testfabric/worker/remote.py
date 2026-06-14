from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from testfabric.core.contracts import RunLease, RunResult, RunStatus, WorkerRegistration
from testfabric.worker.client import WorkerClient
from testfabric.worker.serve import WorkerServeLoop, WorkerServeSummary
from testfabric.worker.runtime import build_run_result_from_raw, execute_lease_with_orchestrator


@dataclass(slots=True)
class RemoteWorkerRuntime:
    client: WorkerClient
    worker: WorkerRegistration | None = None
    executor: Callable[[RunLease], RunResult] | None = None
    heartbeat_interval_seconds: float = 5.0

    def _default_executor(self, lease: RunLease) -> RunResult:
        return execute_lease_with_orchestrator(lease)

    def _build_result(self, lease: RunLease, raw: dict[str, Any]) -> RunResult:
        return build_run_result_from_raw(lease, raw)

    def run_once(
        self,
        *,
        worker_id: str | None = None,
        worker_labels: list[str] | None = None,
        policy: str = "fifo",
    ) -> RunResult | None:
        registration = self.worker
        active_worker_id = worker_id
        active_worker_labels = list(worker_labels or [])
        if registration is not None:
            registered = self.client.register_worker(registration)
            active_worker_id = active_worker_id or registered.worker_id
            if not active_worker_labels:
                active_worker_labels = list(registered.labels or [])
            self.client.heartbeat_worker(
                registered.worker_id,
                metadata={"registered": True},
                health_state=registered.health_state,
                health_reasons=list(registered.health_reasons or []),
            )

        lease = self.client.claim_next_run(
            worker_id=active_worker_id,
            worker_labels=active_worker_labels,
            policy=policy,
        )
        if lease is None:
            return None

        executor = self.executor or self._default_executor
        stop_event = threading.Event()

        def _pulse() -> None:
            interval = max(0.0, float(self.heartbeat_interval_seconds or 0.0))
            if interval <= 0.0:
                return
            while not stop_event.wait(interval):
                self.client.heartbeat_run(lease.run_id, worker_id=lease.claimed_by)
                if registration is not None:
                    self.client.heartbeat_worker(
                        registration.worker_id,
                        metadata={"active_run_id": lease.run_id},
                        health_state=registration.health_state,
                        health_reasons=list(registration.health_reasons or []),
                    )

        heartbeat_thread: threading.Thread | None = None
        if self.heartbeat_interval_seconds and float(self.heartbeat_interval_seconds) > 0:
            self.client.heartbeat_run(lease.run_id, worker_id=lease.claimed_by)
            heartbeat_thread = threading.Thread(target=_pulse, name=f"testfabric-remote-heartbeat-{lease.run_id}", daemon=True)
            heartbeat_thread.start()

        try:
            result = executor(lease)
        finally:
            stop_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2.0)

        self.client.complete_run(lease.run_id, result)
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
