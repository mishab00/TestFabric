# testfabric/execution/executors/docker.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

from testfabric.core.context import StageContext
from testfabric.execution.executors.base import BuildRequest, ExecutableCommand, ExecResult
from testfabric.execution.executors.dockerops import build_image, docker_run


class DockerExecutorAdapter:
    """
    Docker executor implementation.

    Conventions:
      - repo is mounted at ctx.docker.repo_mount (default /work)
      - artifacts_dir (host path) is mounted at cmd.artifacts_root (default /artifacts)
    """

    name = "docker"

    def build(
        self,
        ctx: StageContext,
        req: BuildRequest,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        build_image(
            repo_path=req.repo_path,
            dockerfile=req.dockerfile,
            image=req.image,
            build_args=req.build_args or {},
            no_cache=bool(req.no_cache),
            endpoint=dict(ctx.docker_endpoint or {}),
            on_line=on_line,
        )

    def run(
        self,
        ctx: StageContext,
        cmd: ExecutableCommand,
        *,
        on_stdout: Callable[[str], object] | None = None,
        on_stderr: Callable[[str], object] | None = None,
    ) -> ExecResult:
        repo_mount = (ctx.docker.repo_mount if ctx.docker else "/work").strip() or "/work"

        # resolve container workdir
        workdir = self._resolve_container_workdir(cmd, repo_mount=repo_mount, ctx=ctx)

        # host artifacts dir
        artifacts_dir = (cmd.artifacts_dir or "").strip() or None
        if artifacts_dir:
            Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

        rr = docker_run(
            image=(ctx.docker.image if ctx.docker else ""),
            repo_path=ctx.repo_path,
            repo_mount=repo_mount,
            cmd_inside=list(cmd.cmd),
            env={k: str(v) for k, v in (cmd.env or {}).items()},
            artifacts_dir=artifacts_dir,
            workdir=workdir,
            network=cmd.network,
            shm_size=cmd.shm_size,
            keep_container=bool(cmd.keep),
            endpoint=dict(ctx.docker_endpoint or {}),
            timeout_seconds=cmd.timeout_seconds,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            entrypoint=cmd.entrypoint if cmd.entrypoint is not None else None,
        )
        return ExecResult(
            exit_code=int(rr.exit_code),
            stdout=rr.stdout,
            stderr=rr.stderr,
            timed_out=int(rr.exit_code) == 124,
        )

    def _norm_abs(self, p: str, *, default: str) -> str:
        s = (p or "").strip() or default
        if not s.startswith("/"):
            s = "/" + s
        return s.rstrip("/") or "/"

    def _resolve_container_workdir(self, cmd: ExecutableCommand, *, repo_mount: str, ctx: StageContext) -> str:
        """
        workdir rules:
          - workdir_repo=False: treat cmd.workdir as container path
          - workdir_repo=True:
              * "" or "." -> repo_mount
              * absolute "/x" -> "/x"
              * relative "x/y" -> repo_mount + "/x/y"
        """
        repo_mount = self._norm_abs(repo_mount, default="/work")

        default_workdir = repo_mount
        if ctx.docker and getattr(ctx.docker, "workdir", None):
            default_workdir = self._norm_abs(ctx.docker.workdir, default=repo_mount)

        wd_raw = (cmd.workdir or "").strip()
        wd = wd_raw or default_workdir

        if not cmd.workdir_repo:
            return self._norm_abs(wd, default=default_workdir)

        if not wd or wd == ".":
            return repo_mount

        if wd.startswith("/"):
            return self._norm_abs(wd, default=repo_mount)

        wd_rel = wd.strip().lstrip("./").lstrip("/")
        if not wd_rel:
            return repo_mount
        return f"{repo_mount.rstrip('/')}/{wd_rel}"
