# testfabric/workers/pool.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator
from collections import deque

from testfabric.workers.models import Worker, WorkerMode, WorkerHealthState


@dataclass
class WorkerPool:
    mode: WorkerMode
    capacity: int
    health_state: WorkerHealthState = "healthy"
    health_reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("WorkerPool.capacity must be >= 1")

        # pre-create workers deterministically
        self._workers = deque(
            Worker(
                id=f"w{i:03d}",
                mode=self.mode,
                health_state=self.health_state,
                health_reasons=list(self.health_reasons or []),
            )
            for i in range(self.capacity)
        )

    def allocate(self, n: int) -> list[Worker]:
        """
        Non-exclusive allocation (for read-only / planning use).
        """
        n = max(1, int(n))
        available = [w for w in list(self._workers) if w.health_state != "unhealthy"]
        n = min(n, len(available))
        return available[:n]

    def iter_workers(self, n: int) -> Iterator[Worker]:
        for w in self.allocate(n):
            yield w

    def acquire(self) -> Worker:
        """
        Exclusive lease of one worker.
        Blocks if none are available (MVP: raises instead).
        """
        if not self._workers:
            raise RuntimeError("No workers available in pool")

        for _ in range(len(self._workers)):
            worker = self._workers.popleft()
            if worker.health_state == "unhealthy":
                self._workers.append(worker)
                continue
            return worker
        raise RuntimeError("No healthy workers available in pool")

    def release(self, worker: Worker) -> None:
        """
        Return worker to pool.
        """
        if worker.id not in {w.id for w in self._workers}:
            self._workers.append(worker)
