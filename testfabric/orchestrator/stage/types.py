from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageResult:
    ok: bool
    stage_index: int
    stage_title: str
    stage_slug: str

    suite: str
    kind: str
    executor: str
    runner: str

    run_id: str
    run_dir: str

    worker_id: str
    worker_tmp_dir: str
    worker_mirror_dir: str
    target_id: str = "local"
    target_name: str = "local"
    target_kind: str = "local"
    target_group: str | None = None
    target_address: str | None = None
    target_labels: dict[str, str] | None = None
    transport_type: str | None = None

    stage_summary_path: str | None = None
    stage_summary_rel: str | None = None

    repo_path: str | None = None
    commit_sha: str | None = None

    error: str | None = None
    failure_type: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    lifecycle_mode: str | None = None
    lifecycle_when: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
