from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.events import EventSink, NullEvents
from testfabric.core.envs import merge_env, expand_dict
from testfabric.core.redaction import redact_data, redact_text
from testfabric.artifacts.contract import build_artifact_contract


Mode = Literal["run", "dry-run"]
ExecutorName = Literal["docker", "local"]
RunnerName = Literal["pytest", "command", "expect"]
Kind = Literal["pytest", "command", "expect"]


@dataclass(frozen=True)
class RepoSnapshot:
    repo_path: str
    commit_sha: str
    repo_url: str = "."
    ref: str = "HEAD"


@dataclass(frozen=True)
class OutputsConfig:
    junit: str | None = None
    html: str | None = None


@dataclass(frozen=True)
class DockerRuntime:
    image: str
    mode: Literal["auto", "template", "custom"] = "auto"
    dockerfile: str = "Dockerfile"
    build_args: dict[str, str] = field(default_factory=dict)
    no_cache: bool = False
    base_image: str = "python:3.11-slim"
    python_requirements: list[str] = field(default_factory=list)
    pip_packages: list[str] = field(default_factory=list)
    system_packages: list[str] = field(default_factory=list)

    repo_mount: str = "/work"
    workdir: str = "/work"

    network: str | None = "host"
    shm_size: str | None = "1g"
    env: dict[str, str] = field(default_factory=dict)
    keep_container: bool = False


@dataclass(frozen=True)
class RunContext:
    run_id: str
    mode: Mode
    paths: PathManager
    repo: RepoSnapshot

    events: EventSink = field(default_factory=NullEvents)
    watch: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    resolved_inputs: dict[str, Any] = field(default_factory=dict)
    redaction_values: tuple[str, ...] = field(default_factory=tuple)
    @property
    def run_dir(self) -> str:
        return str(self.paths.run_dir)

    def redact(self, text: str) -> str:
        return redact_text(text, self.redaction_values)

    def redact_data(self, data: Any) -> Any:
        return redact_data(data, self.redaction_values)


@dataclass(frozen=True)
class StageContext:
    run: RunContext

    suite_name: str
    stage_index: int
    stage_title: str
    stage_slug: str
    stage_ref: StageRef

    executor: ExecutorName
    runner: RunnerName
    kind: Kind
    build: bool

    max_workers: int
    chunk_size: int
    max_retries: int

    worker_id: str
    target_id: str = "local"
    target_name: str = "local"
    target_kind: str = "local"
    target_group: str | None = None
    target_address: str | None = None
    target_labels: dict[str, str] = field(default_factory=dict)
    target_credential: str | None = None
    docker_transport: dict[str, object] = field(default_factory=dict)
    docker_endpoint: dict[str, object] = field(default_factory=dict)
    transport_type: str | None = None
    watch: dict[str, Any] = field(default_factory=dict)
    execution_mode: Literal["once", "repeat"] = "once"
    execution_count: int = 1
    split_count: int = 1
    split_manifest_path: str | None = None
    job_timeout_seconds: int | None = None
    stage_timeout_seconds: int | None = None
    lifecycle_mode: str | None = None
    lifecycle_when: str | None = None

    outputs: OutputsConfig = field(default_factory=OutputsConfig)
    docker: DockerRuntime | None = None

    stage_env: dict[str, str] = field(default_factory=dict)  # from StageSection.env (optional)

    # ------------- derived paths -------------

    @property
    def mode(self) -> Mode:
        return self.run.mode

    @property
    def repo_path(self) -> str:
        return self.run.repo.repo_path

    @property
    def commit_sha(self) -> str:
        return self.run.repo.commit_sha

    @property
    def stage_tmp_dir(self) -> str:
        return str(self.run.paths.worker_stage_dir(self.worker_id, self.stage_ref))

    @property
    def stage_logs_dir(self) -> str:
        return str(self.run.paths.worker_stage_logs_dir(self.worker_id, self.stage_ref))

    @property
    def stage_jobs_dir(self) -> str:
        return str(self.run.paths.worker_stage_jobs_dir(self.worker_id, self.stage_ref))

    @property
    def stage_reports_dir(self) -> str:
        return str(self.run.paths.worker_stage_reports_dir(self.worker_id, self.stage_ref))

    @property
    def stage_output_dir(self) -> str:
        return str(self.run.paths.worker_stage_output_dir(self.worker_id, self.stage_ref))

    @property
    def repo_root_in_env(self) -> str:
        """
        Path inside the execution environment that represents the repo root.
        - docker: repo mounted at docker.repo_mount
        - local: executor resolves "." relative to repo_path
        """
        if (self.executor or "").strip().lower() == "docker":
            if self.docker is None:
                raise ValueError("StageContext.docker is required when executor='docker'")
            return (self.docker.repo_mount or "/work").strip() or "/work"
        return "."

    @property
    def artifacts_root_in_env(self) -> str:
        """
        Inside docker we mount artifacts at /artifacts.
        For local, we write directly to ctx.stage_reports_dir.
        """
        if (self.executor or "").strip().lower() == "docker":
            return "/artifacts"
        return self.stage_reports_dir

    @property
    def events(self) -> EventSink:
        return self.run.events

    @property
    def base_env(self) -> dict[str, str]:
        # Runtime env from CLI + (optional) docker.run.env
        docker_env = self.docker.env if self.docker else {}
        return merge_env(self.run.env, docker_env, self.stage_env)


    def suite_env(self, suite_cfg: Any) -> dict[str, str]:
        base = self.base_env
        # expand suite.extra_env using base env
        return merge_env(base, expand_dict(getattr(suite_cfg, "extra_env", {}) or {}, base))

    def job_attempt_output_dir(self, job_id: str, attempt: int) -> str:
        return str(
            self.run.paths.worker_stage_job_attempt_output_dir(
                self.worker_id,
                self.stage_ref,
                job_id,
                attempt,
            )
        )

    def artifact_env(self, *, job_id: str, attempt: int, artifacts_root: str) -> dict[str, str]:
        return build_artifact_contract(
            artifacts_root=artifacts_root,
            run_id=self.run.run_id,
            stage_id=self.stage_slug,
            job_id=job_id,
            attempt=attempt,
        ).as_env()

    def job_env(self, suite_cfg: Any, *, job_id: str, attempt: int, artifacts_root: str) -> dict[str, str]:
        return merge_env(
            self.suite_env(suite_cfg),
            self.artifact_env(job_id=job_id, attempt=attempt, artifacts_root=artifacts_root),
        )
