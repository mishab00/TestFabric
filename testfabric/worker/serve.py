from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from testfabric.core.contracts import RunResult


@dataclass(slots=True)
class WorkerServeSummary:
    loops: int = 0
    runs_claimed: int = 0
    runs_completed: int = 0
    runs_failed: int = 0
    idle_polls: int = 0
    stopped_reason: str | None = None


@dataclass(slots=True)
class WorkerServeLoop:
    runtime: Any
    idle_sleep_seconds: float = 1.0

    def serve(
        self,
        *,
        worker_id: str | None = None,
        worker_labels: list[str] | None = None,
        policy: str = "fifo",
        max_runs: int | None = None,
        max_idle_polls: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> WorkerServeSummary:
        summary = WorkerServeSummary()
        event = stop_event or threading.Event()
        labels = list(worker_labels or [])
        while not event.is_set():
            if max_runs is not None and summary.runs_claimed >= max_runs:
                summary.stopped_reason = "max_runs"
                break
            result = self.runtime.run_once(worker_id=worker_id, worker_labels=labels, policy=policy)
            summary.loops += 1
            if result is None:
                summary.idle_polls += 1
                if max_idle_polls is not None and summary.idle_polls >= max_idle_polls:
                    summary.stopped_reason = "idle"
                    break
                if self.idle_sleep_seconds > 0:
                    time.sleep(float(self.idle_sleep_seconds))
                continue
            summary.runs_claimed += 1
            if isinstance(result, RunResult) and bool(result.ok):
                summary.runs_completed += 1
            elif isinstance(result, RunResult):
                summary.runs_failed += 1
        if event.is_set() and summary.stopped_reason is None:
            summary.stopped_reason = "stopped"
        if summary.stopped_reason is None:
            summary.stopped_reason = "queue_empty" if summary.idle_polls else "complete"
        return summary

