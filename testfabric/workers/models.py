# testfabric/workers/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

WorkerMode = Literal["local", "linode"]
WorkerHealthState = Literal["healthy", "degraded", "unhealthy"]


@dataclass(frozen=True)
class Worker:
    """
    A worker is an execution slot, not an executor.
    Executors run inside a worker (local/docker).
    """
    id: str
    mode: WorkerMode

    # remote backends: where commands run
    host: str | None = None
    user: str | None = None

    # optional metadata for selection/debugging
    labels: dict[str, str] = field(default_factory=dict)
    health_state: WorkerHealthState = "healthy"
    health_reasons: list[str] = field(default_factory=list)
