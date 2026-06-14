# testfabric/orchestrator/plan/models.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExecutorName = Literal["docker", "local"]
RunnerName = Literal["pytest", "command", "expect"]
Kind = Literal["pytest", "command", "expect"]
Mode = Literal["run", "dry-run"]
ExecutionMode = Literal["once", "repeat"]
LifecycleMode = Literal["run", "setup", "teardown"]
LifecycleWhen = Literal["on_success", "always"]


class StagePlan(BaseModel):
    """
    Spec-free plan object.

    IMPORTANT:
      - does NOT embed suite config anymore
      - only references suite by name (suite_name)
      - fully serializable (write plan.json, ship to workers later)
    """

    run_id: str
    mode: Mode

    index: int = Field(ge=1)
    title: str = ""
    suite_name: str

    kind: Kind
    executor: ExecutorName
    runner: RunnerName
    target_id: str = "local"
    target_name: str = "local"
    target_kind: str = "local"
    target_group: str | None = None
    target_address: str | None = None
    target_labels: dict[str, str] = Field(default_factory=dict)
    target_credential: str | None = None
    docker_transport: dict[str, object] = Field(default_factory=dict)
    docker_endpoint: dict[str, object] = Field(default_factory=dict)
    transport_type: str | None = None

    build: bool = False
    execution_mode: ExecutionMode = "once"
    execution_count: int = Field(default=1, ge=1)
    split_count: int = Field(default=1, ge=1)
    split_manifest_path: str | None = None
    job_timeout_seconds: int | None = Field(default=None, ge=1)
    stage_timeout_seconds: int | None = Field(default=None, ge=1)
    lifecycle_mode: LifecycleMode = "run"
    lifecycle_when: LifecycleWhen = "on_success"

    max_workers: int = Field(ge=1)
    chunk_size: int = Field(ge=1)
    max_retries: int = Field(ge=0)

    env: dict[str, str] = Field(default_factory=dict)


class RunPlan(BaseModel):
    run_id: str
    mode: Mode
    stages: list[StagePlan] = Field(default_factory=list)
    watch: dict[str, object] = Field(default_factory=dict)
