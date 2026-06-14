from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RunMode = Literal["run", "dry-run"]
RunState = Literal["queued", "running", "completed", "failed", "canceled", "dry-run"]
ReplayMode = Literal["exact", "failed-only", "from-stage", "from-job"]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    mode: RunMode = "run"
    priority: int = 0
    routing_labels: list[str] = Field(default_factory=list)
    spec_yaml: str | None = None
    spec_path: str | None = None
    spec: dict[str, Any] | None = None
    repo_url: str | None = None
    ref: str | None = None
    team: str | None = None
    project: str | None = None
    workspace: str | None = None
    context: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    mode: str = "remote"
    host: str | None = None
    user: str | None = None
    labels: list[str] = Field(default_factory=list)
    capacity: int = 1
    health_state: str = "healthy"
    health_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    mode: str = "remote"
    host: str | None = None
    user: str | None = None
    labels: list[str] = Field(default_factory=list)
    capacity: int = 1
    active_leases: int = 0
    health_state: str = "healthy"
    health_reasons: list[str] = Field(default_factory=list)
    last_heartbeat_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: RunState = "queued"
    verdict: str | None = None
    message: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    heartbeat_at: str | None = None
    stage_id: str | None = None
    job_id: str | None = None
    attempt: int | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    accepted: bool = True
    status: RunStatus | None = None
    message: str | None = None
    links: dict[str, str] = Field(default_factory=dict)


class RunLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    request: RunRequest
    response: RunResponse
    claimed_by: str | None = None
    claimed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    ok: bool
    verdict: str | None = None
    state: RunState = "completed"
    run_dir: str | None = None
    summary_path: str | None = None
    error: str | None = None
    status: RunStatus | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: str | None = None
    project: str | None = None
    workspace: str | None = None
    branch: str | None = None
    ref: str | None = None
    status: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    target_id: str | None = None
    target_name: str | None = None
    limit: int = 50
    offset: int = 0
    sort: str | None = None


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_run_id: str
    right_run_id: str
    fields: list[str] = Field(default_factory=list)
    team: str | None = None
    project: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    mode: ReplayMode = "exact"
    stage_id: str | None = None
    job_id: str | None = None
    local: bool = False
    overrides: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
