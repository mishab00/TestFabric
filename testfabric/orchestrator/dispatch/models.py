# testfabric/dispatch/models.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchJobResult:
    job_id: str
    attempt: int
    exit_code: int
    started: bool = True
    started_at: str | None = None
    ended_at: str | None = None
    timed_out: bool = False
    timeout_scope: str | None = None
    error: str | None = None
    items_count: int = 0
    item_ids: list[str] | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class DispatchResult:
    results: list[DispatchJobResult]
    failed_job_ids: list[str]
    timed_out_job_ids: list[str]
    watch_failure_reason: str | None = None
