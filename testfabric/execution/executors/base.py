# testfabric/execution/executors/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Callable, Optional


@dataclass(frozen=True)
class BuildRequest:
    repo_path: str
    dockerfile: str
    image: str
    build_args: dict[str, str] = field(default_factory=dict)
    no_cache: bool = False


@dataclass(frozen=True)
class ExecutableCommand:
    """
    Canonical command object used everywhere.
    - Runner produces this
    - Dispatcher passes it
    - ExecutorAdapter executes it
    """
    cmd: list[str]

    env: dict[str, str] = field(default_factory=dict)

    # workdir semantics:
    # - workdir_repo=True => interpreted relative to repo root (local) / repo_mount (docker)
    # - workdir_repo=False => interpreted as absolute container path (docker) or absolute host path (local)
    workdir: str = "."
    workdir_repo: bool = True

    # Host path for artifacts (bind-mounted to artifacts_root in docker)
    artifacts_dir: str | None = None

    # Path inside the execution environment where outputs should be written
    # - docker: typically "/artifacts"
    # - local: can be host path too, but we keep it as a string contract
    artifacts_root: str = "/artifacts"

    # docker-only runtime options (safe to keep here)
    network: str | None = None
    shm_size: str | None = None
    keep: bool = False
    entrypoint: str | None = ""
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class ExecutorAdapter(Protocol):
    name: str

    def build(
        self,
        ctx,
        req: BuildRequest,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> None: ...

    def run(
        self,
        ctx,
        cmd: ExecutableCommand,
        *,
        on_stdout: Callable[[str], object] | None = None,
        on_stderr: Callable[[str], object] | None = None,
    ) -> ExecResult: ...
