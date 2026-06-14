from __future__ import annotations

# testfabric/spec/schema.py
import os
import re
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import PrivateAttr

from testfabric.inputs.models import InputDefinition, ProfileDefinition, ResolvedInputs
from testfabric.inputs.profiles import apply_profile_overlay
from testfabric.inputs.resolver import InputResolver
from testfabric.spec.parametrize import expand_data
from testfabric.workers.targets import dump_grouped_targets, load_targets_mapping


# ----------------------------
# Core
# ----------------------------

class RunSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="adhoc")
    repo_url: str = Field(default=".")
    ref: str = Field(default="HEAD")
    workdir: str = Field(default=".testfabric/work")
    artifacts_dir: str = Field(default="artifacts")
    console_verbosity: Literal["quiet", "summary", "normal", "verbose"] = Field(default="normal")

    # artifacts layout
    runs_subdir: str = Field(default="runs")                   # <artifacts_dir>/<runs_subdir>/<run_id>/
    workers_tmp: str | None = None                             # staging root; defaults to <artifacts_dir>/workers_tmp
    run_id: str | None = None                                  # optional fixed run id (CI)

    @field_validator("workers_tmp", mode="before")
    @classmethod
    def normalize_workers_tmp(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        return text or None


# ----------------------------
# Workers (NEW)
# ----------------------------

WorkersMode = Literal["local", "linode"]


class DockerTransport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """
    Remote Docker transport defaults. Keep this minimal for now.
    Later you can add transport tuning, endpoint aliases, and similar options.
    """
    base_url: str | None = None
    user: str = Field(default="root")
    port: int = Field(default=22, ge=1, le=65535)
    key_path: str | None = None
    password: str | None = None
    known_hosts: str | None = None
    via: str | None = None
    tls: bool | dict[str, str] | None = None

    # Optional: environment variables / options
    # (Keep future-proof but not required)
    extra_args: List[str] = Field(default_factory=list)


_SELECTOR_RE = re.compile(r"^(all|group:[A-Za-z0-9_.-]+|host:[A-Za-z0-9_.-]+)$")


class WorkersSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: WorkersMode = Field(default="local")

    # Pool capacity (how many workers exist / can be used).
    # When omitted, local execution derives a capacity from requested parallelism.
    max_workers: int | None = Field(default=None, ge=1)

    # Where workers keep their repo/artifacts (used for remote execution backends).
    # For local mode you can ignore it.
    workdir: str | None = Field(default=None)

    transport: DockerTransport | None = Field(default=None)

    # Future (optional; not used in MVP)
    reuse: bool = Field(default=False)
    ttl: str | None = Field(default=None)          # e.g. "2h" (keep as str for now)

    @model_validator(mode="after")
    def validate_worker_requirements(self):
        return self


class TargetHostSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    labels: Dict[str, str] = Field(default_factory=dict)
    credential: str | None = None
    transport: DockerTransport | None = None


class TargetsSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str | None = None

    def group_map(self) -> dict[str, dict[str, TargetHostSection]]:
        groups: dict[str, dict[str, TargetHostSection]] = {}
        extras = dict(getattr(self, "__pydantic_extra__", {}) or {})
        for raw_group, raw_hosts in extras.items():
            group = str(raw_group).strip()
            if not group or group == "file":
                continue
            if not isinstance(raw_hosts, dict):
                raise ValueError(f"targets.{group} must be a mapping of host name to host config")
            group_hosts: dict[str, TargetHostSection] = {}
            for raw_host_id, raw_host in raw_hosts.items():
                host_id = str(raw_host_id).strip()
                if not host_id:
                    continue
                payload = dict(raw_host or {}) if isinstance(raw_host, dict) else {}
                payload.setdefault("name", host_id)
                group_hosts[host_id] = TargetHostSection.model_validate(payload)
            groups[group] = group_hosts
        return groups

    @model_validator(mode="after")
    def validate_groups(self):
        self.group_map()
        return self


class CredentialsSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str | None = None

    def credential_map(self) -> dict[str, DockerTransport]:
        creds: dict[str, DockerTransport] = {}
        extras = dict(getattr(self, "__pydantic_extra__", {}) or {})
        for raw_name, raw_cred in extras.items():
            name = str(raw_name).strip()
            if not name or name == "file":
                continue
            payload = dict(raw_cred or {}) if isinstance(raw_cred, dict) else {}
            creds[name] = DockerTransport.model_validate(payload)
        return creds

    @model_validator(mode="after")
    def validate_credentials(self):
        self.credential_map()
        return self


# ----------------------------
# Executors config (optional section you already had)
# ----------------------------

class LocalExecutorSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workdir: str = Field(default=".")
    env: Dict[str, str] = Field(default_factory=dict)


class ExecutorsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local: LocalExecutorSection = Field(default_factory=LocalExecutorSection)
    # docker remains top-level; remote Docker transport defaults can live under workers.transport for now


# ----------------------------
# Suites
# ----------------------------

ExecutorName = Literal["docker", "local"]
RunnerName = Literal["pytest", "command", "expect"]

class CommandStepSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cmd: List[str] | None = None
    bash: str | None = None
    shell: str | None = None
    stdout_to: str | None = None
    stderr_to: str | None = None
    workdir: str = Field(default=".")
    env: Dict[str, str] = Field(default_factory=dict)
    continue_on_fail: bool = False
    always: bool = False

    @field_validator("bash", "shell", mode="before")
    @classmethod
    def normalize_script_field(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            parts = [str(x) for x in v if str(x).strip()]
            return "\n".join(parts).strip() or None
        text = str(v)
        return text.strip() or None

    @field_validator("stdout_to", "stderr_to", mode="before")
    @classmethod
    def normalize_capture_path(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        if not text:
            return None
        if text.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", text):
            raise ValueError("capture path must be relative to TESTFABRIC_ARTIFACTS_DIR")
        parts = PurePosixPath(text.replace("\\", "/")).parts
        if any(part == ".." for part in parts):
            raise ValueError("capture path must not escape TESTFABRIC_ARTIFACTS_DIR")
        return text

    @model_validator(mode="after")
    def validate_command_shape(self):
        provided = 0
        if self.cmd:
            provided += 1
        if self.bash:
            provided += 1
        if self.shell:
            provided += 1
        if provided != 1:
            raise ValueError("command step must define exactly one of: cmd, bash, shell")
        return self


class PytestSuiteSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pytest"] = "pytest"
    executor: ExecutorName | None = None
    runner: RunnerName | None = None
    path: str
    rootdir: str | None = None
    select: list[str] = Field(default_factory=list)
    args: str = Field(default="")
    markers: str = Field(default="")
    extra_env: Dict[str, str] = Field(default_factory=dict)

    @field_validator("select", mode="before")
    @classmethod
    def normalize_select(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            return [] if not s else [s]
        if isinstance(v, list):
            out: list[str] = []
            for x in v:
                if x is None:
                    continue
                s = str(x).strip()
                if s:
                    out.append(s)
            return out
        return v


class CommandSuiteSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["command"] = "command"
    executor: ExecutorName | None = None
    runner: RunnerName | None = None
    cmd: List[str] | None = None
    bash: str | None = None
    shell: str | None = None
    workdir: str = Field(default=".")
    env: Dict[str, str] = Field(default_factory=dict)
    expect: List["ExpectStepSection"] = Field(default_factory=list)
    steps: List[CommandStepSection] = Field(default_factory=list)
    dry_steps: List[CommandStepSection] = Field(default_factory=list)

    @field_validator("bash", "shell", mode="before")
    @classmethod
    def normalize_script_field(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            parts = [str(x) for x in v if str(x).strip()]
            return "\n".join(parts).strip() or None
        text = str(v)
        return text.strip() or None

    @model_validator(mode="after")
    def validate_command_shape(self):
        if self.expect:
            provided = 0
            if self.cmd:
                provided += 1
            if self.bash:
                provided += 1
            if self.shell:
                provided += 1
            if provided != 1:
                raise ValueError("interactive command suite must define exactly one of: cmd, bash, shell")
            if self.steps or self.dry_steps:
                raise ValueError("interactive command suite cannot define steps or dry_steps")
            return self
        if not self.steps:
            raise ValueError("command suite requires at least one step")
        return self


class ExpectPromptSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: str | None = None
    regex: str | None = None
    stream: Literal["stdout", "stderr", "both"] = Field(default="stdout")

    @model_validator(mode="after")
    def validate_expect_prompt_shape(self):
        if not self.match and not self.regex:
            raise ValueError("expect step.when requires match or regex")
        return self


class ExpectSendSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    send: str


class ExpectStepSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    when: ExpectPromptSection
    then: ExpectSendSection


class ExpectSuiteSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["expect"] = "expect"
    executor: ExecutorName | None = None
    runner: RunnerName | None = None
    cmd: List[str] | None = None
    bash: str | None = None
    shell: str | None = None
    workdir: str = Field(default=".")
    env: Dict[str, str] = Field(default_factory=dict)
    steps: List[ExpectStepSection] = Field(default_factory=list)

    @field_validator("bash", "shell", mode="before")
    @classmethod
    def normalize_script_field(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            parts = [str(x) for x in v if str(x).strip()]
            return "\n".join(parts).strip() or None
        text = str(v)
        return text.strip() or None

    @model_validator(mode="after")
    def validate_expect_shape(self):
        provided = 0
        if self.cmd:
            provided += 1
        if self.bash:
            provided += 1
        if self.shell:
            provided += 1
        if provided != 1:
            raise ValueError("expect suite must define exactly one of: cmd, bash, shell")
        if not self.steps:
            raise ValueError("expect suite requires at least one step")
        return self


SuiteSection = Union[PytestSuiteSection, CommandSuiteSection, ExpectSuiteSection]


# ----------------------------
# Docker
# ----------------------------

class DockerRunSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shm_size: str = Field(default="1g")
    network: Optional[str] = Field(default="host")
    env: Dict[str, str] = Field(default_factory=dict)
    keep_container: bool = Field(default=False)


class DockerSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "template", "custom"] = Field(default="auto")
    dockerfile: str = Field(default="Dockerfile")
    image: str = Field(default="testfabric-tests:ci")
    base_image: str = Field(default="python:3.11-slim")
    python_requirements: List[str] = Field(default_factory=list)
    pip_packages: List[str] = Field(default_factory=list)
    system_packages: List[str] = Field(default_factory=list)
    repo_mount: str = Field(default="/work")
    workdir: str = Field(default="/work")
    build_args: Dict[str, str] = Field(default_factory=dict)
    no_cache: bool = False
    run: DockerRunSection = Field(default_factory=DockerRunSection)


# ----------------------------
# Parallelism / Outputs
# ----------------------------

class ParallelismSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_workers: int = Field(default=1, ge=1)
    chunk_size: int = Field(default=25, ge=1)
    max_retries: int = Field(default=0, ge=0)


class OutputsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    junit: str | None = None
    html: str | None = None


# ----------------------------
# Execution
# ----------------------------

class ExecutionSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["once", "repeat"] = Field(default="once")
    count: int = Field(default=1, ge=1)


class SplitSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=1, ge=1)
    manifest_path: str | None = None

    @model_validator(mode="after")
    def validate_split_shape(self):
        if self.manifest_path and self.count != 1:
            raise ValueError("split.count cannot be combined with split.manifest_path")
        return self


class TimeoutSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_seconds: int | None = Field(default=None, ge=1)
    stage_seconds: int | None = Field(default=None, ge=1)


# ----------------------------
# Health
# ----------------------------

class HealthSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_docker: bool = False
    min_disk_gb: int = Field(default=1, ge=0)
    min_mem_gb: int | None = Field(default=None, ge=0)


# ----------------------------
# Watch
# ----------------------------

WatchPhase = Literal["setup", "run", "stage", "job", "teardown"]
WatchSourceType = Literal["session", "file", "http", "container_logs", "remote_file", "metric"]
WatchActionType = Literal["fail", "success", "report", "notify", "send", "script", "stop", "continue"]


class WatchActionSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    type: WatchActionType = Field(default="report")
    value: str | None = None
    message: str | None = None
    command: List[str] | None = None
    level: Literal["info", "warn", "error"] = Field(default="info")
    stop_all: bool = Field(default=False)
    once: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_action_shape(self):
        if self.type == "script" and not self.command:
            raise ValueError("watch action type 'script' requires command")
        if self.type == "send" and not self.value:
            raise ValueError("watch action type 'send' requires value")
        return self


class WatchReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None = None


class WatchWhenSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: str | None = None
    sequence: List[str] = Field(default_factory=list)
    status: int | List[int] | None = None
    gt: float | None = None
    gte: float | None = None
    lt: float | None = None
    lte: float | None = None

    @field_validator("match", mode="before")
    @classmethod
    def normalize_match(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            items: list[int] = []
            for item in v:
                text = str(item).strip()
                if not text:
                    continue
                items.append(int(text))
            return items or None
        text = str(v).strip()
        if not text:
            return None
        return int(text)

    @field_validator("sequence", mode="before")
    @classmethod
    def normalize_sequence(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            items: list[str] = []
            for item in v:
                text = str(item).strip()
                if text:
                    items.append(text)
            return items
        text = str(v).strip()
        return [text] if text else []

    @field_validator("gt", "gte", "lt", "lte", mode="before")
    @classmethod
    def normalize_threshold(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        if not text:
            return None
        return float(text)

    @model_validator(mode="after")
    def validate_when_shape(self):
        if self.sequence and any(
            value is not None
            for value in (self.match, self.status, self.gt, self.gte, self.lt, self.lte)
        ):
            raise ValueError("watch when.sequence cannot be combined with other conditions")
        if (
            self.match is None
            and not self.sequence
            and self.status is None
            and self.gt is None
            and self.gte is None
            and self.lt is None
            and self.lte is None
        ):
            raise ValueError("watch when requires at least one condition")
        return self


class WatchThenSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: WatchReportSection | str | None = None
    shell: str | None = None
    bash: str | None = None
    command: List[str] | None = None
    send: str | None = None
    fail: Literal["immediate"] | bool | None = None
    success: bool | None = None

    @field_validator("report", "shell", "bash", "send", mode="before")
    @classmethod
    def normalize_text_fields(cls, v):
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        text = str(v).strip()
        return text or None

    @field_validator("command", mode="before")
    @classmethod
    def normalize_command(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            items = [str(item).strip() for item in v if str(item).strip()]
            return items or None
        text = str(v).strip()
        return [text] if text else None

    @field_validator("fail", mode="before")
    @classmethod
    def normalize_fail(cls, v):
        if v in (None, False, "", 0):
            return None
        if isinstance(v, str):
            text = v.strip().lower()
            if not text or text in {"false", "no", "off"}:
                return None
            return "immediate"
        return "immediate"

    @field_validator("success", mode="before")
    @classmethod
    def normalize_success(cls, v):
        if v in (None, False, "", 0):
            return None
        return True

    @model_validator(mode="after")
    def validate_then_shape(self):
        provided = 0
        if self.report is not None:
            provided += 1
        if self.shell:
            provided += 1
        if self.bash:
            provided += 1
        if self.command:
            provided += 1
        if self.send:
            provided += 1
        if self.fail is not None:
            provided += 1
        if self.success is not None:
            provided += 1
        if provided != 1:
            raise ValueError("watch then requires exactly one action")
        return self


class WatchSourceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: WatchSourceType
    phases: List[WatchPhase] = Field(default_factory=lambda: ["run"])
    path: str | None = None
    url: str | None = None
    cmd: List[str] | None = None
    bash: str | None = None
    shell: str | None = None
    host: str | None = None
    user: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    key_path: str | None = None
    known_hosts: str | None = None
    via: str | None = None
    stream: Literal["stdout", "stderr", "both"] = Field(default="both")
    mode: Literal["tail", "snapshot", "poll"] = Field(default="tail")
    interval_seconds: int | None = Field(default=None, ge=1)
    target: str | None = None

    @model_validator(mode="after")
    def validate_watch_source_shape(self):
        if self.type == "remote_file":
            if not self.path:
                raise ValueError("watch source type 'remote_file' requires path")
            if not self.host and not self.target:
                raise ValueError("watch source type 'remote_file' requires host or target")
        if self.type == "metric":
            provided = 0
            if self.cmd:
                provided += 1
            if self.bash:
                provided += 1
            if self.shell:
                provided += 1
            if provided != 1:
                raise ValueError("watch source type 'metric' requires exactly one of: cmd, bash, shell")
            if self.mode != "poll":
                raise ValueError("watch source type 'metric' requires mode 'poll'")
        return self


class WatcherSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str | None = None
    sources: List[str] = Field(default_factory=list)
    phases: List[WatchPhase] = Field(default_factory=lambda: ["run"])
    when: WatchWhenSection | None = None
    then: WatchThenSection | None = None
    match: str | None = None
    regex: str | None = None
    body_match: str | None = None
    status: List[int] = Field(default_factory=list)
    once: bool = Field(default=False)
    dedupe: bool = Field(default=True)
    missing: Literal["ignore", "warn", "fail"] = Field(default="ignore")
    action: str | WatchActionSection | None = None

    @model_validator(mode="after")
    def validate_watcher_shape(self):
        if not self.source and not self.sources:
            raise ValueError("watch watcher requires source or sources")
        if self.when is None and not self.match and not self.regex and not self.body_match and not self.status:
            raise ValueError("watch watcher requires at least one match condition")
        if self.then is None and self.action is None:
            raise ValueError("watch watcher requires then or action")
        if self.when and self.when.sequence:
            refs = [self.source] if self.source else []
            refs.extend(self.sources or [])
            if len([ref for ref in refs if str(ref or "").strip()]) != 1:
                raise ValueError("watch watcher with sequence requires exactly one source")
        return self


class WatchSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phases: List[WatchPhase] = Field(default_factory=lambda: ["run"])
    sources: List[WatchSourceSection] = Field(default_factory=list)
    actions: List[WatchActionSection] = Field(default_factory=list)
    watchers: List[WatcherSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_watch_refs(self):
        source_names = {s.name for s in self.sources}
        action_names = {a.name for a in self.actions if (a.name or "").strip()}
        builtin_actions = {"fail", "success", "report", "notify", "send", "script", "stop", "continue"}

        for watcher in self.watchers:
            refs = [watcher.source] if watcher.source else []
            refs.extend(watcher.sources or [])
            unknown_sources = [ref for ref in refs if ref not in source_names]
            if unknown_sources:
                raise ValueError(
                    f"watch watcher '{watcher.name}' references unknown source(s): {sorted(set(unknown_sources))}"
                )

            if watcher.action is None:
                continue
            if isinstance(watcher.action, str):
                action_ref = watcher.action.strip()
                if action_ref not in builtin_actions and action_ref not in action_names:
                    raise ValueError(
                        f"watch watcher '{watcher.name}' references unknown action '{action_ref}'"
                    )
            else:
                action_name = str(getattr(watcher.action, "name", "") or "").strip()
                if action_name and action_name not in action_names:
                    # inline actions are allowed without names; named inline actions must still be declared
                    action_names.add(action_name)

        return self


# ----------------------------
# Pipeline
# ----------------------------

class StageLifecycleSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["run", "setup", "teardown"] = Field(default="run")
    when: Literal["on_success", "always"] = Field(default="on_success")


class StageSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="")
    suite: str  # REQUIRED: key in suites map

    executor: Optional[Literal["docker", "local"]] = None
    runner: Optional[str] = None
    targets: str | None = None
    env: Dict[str, str] = Field(default_factory=dict)
    execution: ExecutionSection = Field(default_factory=ExecutionSection)
    split: SplitSection = Field(default_factory=SplitSection)
    timeout: TimeoutSection = Field(default_factory=TimeoutSection)
    lifecycle: StageLifecycleSection = Field(default_factory=StageLifecycleSection)

    build: bool = False

    # Rare stage-local parallelism overrides (keep for now)
    max_workers: Optional[int] = None
    chunk_size: Optional[int] = None
    max_retries: Optional[int] = None

    @field_validator("targets")
    @classmethod
    def validate_targets_selector(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if s == "all":
            return s
        if _SELECTOR_RE.match(s):
            return s
        if re.match(r"^[A-Za-z0-9_.-]+$", s):
            return s
        raise ValueError("stage.targets must be a target group name or one of: 'all', 'group:<name>', 'host:<name>'")


class PipelineSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: List[StageSection] = Field(default_factory=list)
    default_executor: Optional[Literal["docker", "local"]] = None


# ----------------------------
# Root spec
# ----------------------------

class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _source_path: str | None = PrivateAttr(default=None)

    run: RunSection = Field(default_factory=RunSection)

    inputs: Dict[str, InputDefinition] = Field(default_factory=dict)
    profiles: Dict[str, ProfileDefinition] = Field(default_factory=dict)

    workers: WorkersSection = Field(default_factory=WorkersSection)
    targets: TargetsSection = Field(default_factory=TargetsSection)
    credentials: CredentialsSection = Field(default_factory=CredentialsSection)

    pipeline: PipelineSection
    watch: WatchSection | None = None
    executors: ExecutorsSection = Field(default_factory=ExecutorsSection)

    suites: Dict[str, SuiteSection]

    docker: DockerSection = Field(default_factory=DockerSection)
    health: HealthSection = Field(default_factory=HealthSection)
    parallelism: ParallelismSection = Field(default_factory=ParallelismSection)
    outputs: OutputsSection = Field(default_factory=OutputsSection)

    def effective_worker_capacity(self) -> int:
        if self.workers.max_workers is not None:
            return int(self.workers.max_workers)

        requested = [int(self.parallelism.max_workers)]
        for st in self.pipeline.stages:
            if st.max_workers is not None:
                requested.append(int(st.max_workers))
        return max(requested or [1])

    def target_group_map(self) -> dict[str, dict[str, TargetHostSection]]:
        return self.targets.group_map()

    def credential_map(self) -> dict[str, DockerTransport]:
        return self.credentials.credential_map()

    @model_validator(mode="after")
    def validate_cross_fields(self):
        # Make sure pipeline stages refer to known suites
        suites = set(self.suites.keys())
        for i, st in enumerate(self.pipeline.stages):
            if st.suite not in suites:
                raise ValueError(f"pipeline.stages[{i}].suite='{st.suite}' not found in suites: {sorted(suites)}")

        # When worker capacity is explicitly set, do not allow stage/global concurrency to exceed it.
        if self.workers and self.parallelism and self.workers.max_workers is not None:
            if self.parallelism.max_workers > self.workers.max_workers:
                raise ValueError(
                    f"parallelism.max_workers ({self.parallelism.max_workers}) "
                    f"cannot exceed workers.max_workers ({self.workers.max_workers})"
                )
        if self.workers.mode in {"linode"}:
            raise ValueError(
                f"workers.mode='{self.workers.mode}' is defined in schema but not implemented at runtime yet"
            )
        for i, st in enumerate(self.pipeline.stages):
            ex = (st.executor or "").strip().lower()
            if ex in {"linode"}:
                raise ValueError(
                    f"pipeline.stages[{i}].executor='{ex}' is not implemented yet"
                )
        return self

    @staticmethod
    def _load_yaml(path: str) -> dict[str, Any]:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Spec YAML must parse to a mapping/dict at top-level.")
        return data

    @staticmethod
    def _merge_external_mapping(
        raw: dict[str, Any],
        *,
        key: str,
        spec_path: str,
        expansion_values: dict[str, str],
    ) -> dict[str, Any]:
        section = raw.get(key)
        if not isinstance(section, dict):
            return raw
        file_path = str(section.get("file") or "").strip()
        if not file_path:
            return raw

        base_dir = Path(spec_path).expanduser().resolve().parent
        candidate = Path(file_path).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        if not candidate.exists():
            raise ValueError(f"{key}.file not found: {candidate}")

        loaded = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{key}.file must parse to a mapping/dict at top-level")
        loaded = expand_data(loaded, expansion_values)
        if not isinstance(loaded, dict):
            raise ValueError(f"Expanded {key}.file must remain a mapping/dict at top-level")
        if key == "targets":
            loaded = dump_grouped_targets(load_targets_mapping(loaded))

        merged = dict(loaded)
        for raw_key, raw_value in section.items():
            if raw_key == "file":
                continue
            merged[raw_key] = raw_value
        raw[key] = merged
        return raw

    @staticmethod
    def load_resolved(
        path: str,
        *,
        profile: str | None = None,
        inputs_map: dict[str, str] | None = None,
        include_os_env: bool = True,
    ) -> tuple["RunSpec", ResolvedInputs]:
        raw = RunSpec._load_yaml(path)
        merged, selected_profile = apply_profile_overlay(raw, profile_name=profile)

        resolver = InputResolver()
        resolved_inputs = resolver.resolve(
            input_defs=merged.get("inputs") or {},
            profile=selected_profile,
            cli_inputs=inputs_map,
            env=os.environ if include_os_env else {},
        )

        expansion_values: dict[str, str] = {}
        expansion_values.update(resolved_inputs.as_expansion_map())

        merged = RunSpec._merge_external_mapping(
            merged,
            key="targets",
            spec_path=path,
            expansion_values=expansion_values,
        )
        merged = RunSpec._merge_external_mapping(
            merged,
            key="credentials",
            spec_path=path,
            expansion_values=expansion_values,
        )

        expanded = expand_data(merged, expansion_values)
        if not isinstance(expanded, dict):
            raise ValueError("Expanded spec YAML must remain a mapping/dict at top-level.")

        spec = RunSpec.model_validate(expanded)
        spec._source_path = str(Path(path).expanduser().resolve())
        return spec, resolved_inputs

    @staticmethod
    def load(
        path: str,
        *,
        profile: str | None = None,
        inputs_map: dict[str, str] | None = None,
        include_os_env: bool = True,
    ) -> "RunSpec":
        spec, _ = RunSpec.load_resolved(
            path,
            profile=profile,
            inputs_map=inputs_map,
            include_os_env=include_os_env,
        )
        return spec
