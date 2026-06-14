from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageHistoryRecord:
    run_id: str
    repo_url: str
    ref: str
    commit_sha: str | None
    stage_index: int
    stage_title: str
    stage_slug: str
    suite: str
    kind: str
    executor: str
    runner: str
    worker_id: str | None
    ok: bool
    failure_type: str | None
    skipped: bool
    skip_reason: str | None
    lifecycle_mode: str | None
    lifecycle_when: str | None
    duration_seconds: float | None
    jobs_total: int | None
    jobs_failed: int | None


@dataclass(frozen=True)
class JobHistoryRecord:
    run_id: str
    stage_slug: str
    suite: str
    executor: str
    runner: str
    job_id: str
    attempt: int
    exit_code: int
    timed_out: bool
    timeout_scope: str | None
    error: str | None
    items_count: int
    item_ids_json: str
    duration_seconds: float | None
