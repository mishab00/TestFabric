# testfabric/suites/pytest.py
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Iterable, List, Any
from testfabric.core.context import StageContext
from testfabric.execution.executors.base import ExecutableCommand
from testfabric.execution.suites.base import TestItem, JobPlan
from testfabric.core.envs import expand_dict, merge_env


def parse_nodeids(stdout: str) -> List[str]:
    nodeids: List[str] = []
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("=") or line.startswith("WARNING") or line.startswith("DeprecationWarning"):
            continue
        if line.startswith("collected "):
            continue
        if " in " in line and line.endswith("s"):
            continue
        if "::" in line:
            nodeids.append(line)
            continue
        if ":" in line:
            left, right = line.split(":", 1)
            left = left.strip()
            right = right.strip()
            if left.endswith(".py") and right.isdigit():
                nodeids.append(left)
                continue
        if line.endswith(".py") and ("/" in line or line.startswith("tests")):
            nodeids.append(line)
            continue

    seen = set()
    out: List[str] = []
    for n in nodeids:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _expand_env_vars(d: dict[str, str]) -> dict[str, str]:
    return {k: os.path.expandvars(str(v)) for k, v in (d or {}).items()}


def _join_path(base: str, rel: str) -> str:
    base = (base or "").rstrip("/")
    rel = (rel or "").lstrip("/")
    if not base:
        return rel
    if not rel:
        return base
    return f"{base}/{rel}"


def _is_abs_container_path(s: str) -> bool:
    return (s or "").startswith("/")


def _looks_like_nodeid(s: str) -> bool:
    return "::" in (s or "")


def _normalize_rootdir(base: str, rootdir: str | None) -> str | None:
    if not rootdir:
        return None
    r = str(rootdir).strip()
    if not r:
        return None
    return r if _is_abs_container_path(r) else _join_path(base, r)


def _normalize_suite_path(base: str, suite_path: str) -> str:
    sp = (suite_path or "").strip().lstrip("/")
    return _join_path(base, sp) if sp else base


def _normalize_selection_targets(base: str, suite_path: str, select: list[str] | None) -> list[str]:
    suite_abs = _normalize_suite_path(base, suite_path)
    if not select:
        return [suite_abs]

    out: list[str] = []
    suite_rel = (suite_path or "").strip().strip("/")

    for raw in select:
        s = (raw or "").strip()
        if not s:
            continue

        if _is_abs_container_path(s):
            out.append(s)
            continue

        if _looks_like_nodeid(s):
            left, right = s.split("::", 1)
            left = left.strip()

            if suite_rel and (left == suite_rel or left.startswith(suite_rel + "/")):
                left = left[len(suite_rel):].lstrip("/")

            left = _join_path(suite_abs, left) if left else suite_abs
            out.append(f"{left}::{right.strip()}")
            continue

        if suite_rel and (s == suite_rel or s.startswith(suite_rel + "/")):
            s = s[len(suite_rel):].lstrip("/")
        out.append(_join_path(suite_abs, s) if s else suite_abs)

    return out or [suite_abs]


def _normalize_collected_nodeids(base: str, suite_path: str, nodeids: Iterable[str]) -> list[str]:
    abs_suite = _normalize_suite_path(base, suite_path)
    suite_rel = (suite_path or "").strip().strip("/")

    out: list[str] = []
    for raw in nodeids:
        n = (raw or "").strip()
        if not n:
            continue

        if n.startswith("/"):
            out.append(n)
            continue

        if _looks_like_nodeid(n):
            left, right = n.split("::", 1)
            left = left.strip()

            if suite_rel and (left == suite_rel or left.startswith(suite_rel + "/")):
                left = _join_path(base, left)
            else:
                left = _join_path(abs_suite, left)

            out.append(f"{left}::{right.strip()}")
        else:
            if suite_rel and (n == suite_rel or n.startswith(suite_rel + "/")):
                out.append(_join_path(base, n))
            else:
                out.append(_join_path(abs_suite, n))

    return out


def _scoped_output_name(name: str, job_id: str, attempt: int) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return f"{stem}-{job_id}-a{attempt}.{ext}"
    return f"{name}-{job_id}-a{attempt}"


def _pytest_command_prefix(ctx: StageContext) -> list[str]:
    if (ctx.executor or "").strip().lower() == "local":
        return [sys.executable, "-m", "pytest"]
    return ["pytest"]


class PytestRunnerAdapter:
    name = "pytest"

    def collect(self, ctx: StageContext, suite_cfg: Any, executor: Any) -> list[TestItem]:
        suite = suite_cfg
        if suite.kind != "pytest":
            raise TypeError(f"PytestRunnerAdapter requires suite.kind='pytest', got {suite.kind!r}")

        base = ctx.repo_root_in_env

        cmd: list[str] = [*_pytest_command_prefix(ctx), "--collect-only", "-q"]

        rootdir = _normalize_rootdir(base, suite.rootdir)
        if rootdir:
            cmd += [f"--rootdir={rootdir}"]

        if suite.markers:
            cmd += ["-m", suite.markers]

        if suite.args:
            cmd += suite.args.split()

        cmd += _normalize_selection_targets(base, suite.path, getattr(suite, "select", None))

        Path(ctx.stage_logs_dir).mkdir(parents=True, exist_ok=True)
        (Path(ctx.stage_logs_dir) / "collect-cmd.txt").write_text(ctx.run.redact(" ".join(cmd) + "\n"), encoding="utf-8")

        r = executor.run(
            ctx,
            ExecutableCommand(
                cmd=cmd,
                env=self._env(ctx, suite),
                workdir=".",
                workdir_repo=True,
                artifacts_dir=None,               # collect doesn't need artifacts mount
                artifacts_root="/artifacts",      # irrelevant here
                network=(ctx.docker.network if ctx.docker else None),
                shm_size=(ctx.docker.shm_size if ctx.docker else None),
                keep=False,
                entrypoint="",
            ),
        )

        # after executor.run(...)
        (Path(ctx.stage_logs_dir) / "collect-exit_code.txt").write_text(str(r.exit_code) + "\n", encoding="utf-8")
        (Path(ctx.stage_logs_dir) / "collect-stdout.log").write_text(ctx.run.redact(r.stdout or ""), encoding="utf-8")
        (Path(ctx.stage_logs_dir) / "collect-stderr.log").write_text(ctx.run.redact(r.stderr or ""), encoding="utf-8")

        if int(r.exit_code) != 0:
            raise RuntimeError(
                "pytest collect failed "
                f"(exit_code={r.exit_code}). See logs: "
                f"{ctx.stage_logs_dir}/collect-stderr.log"
            )

        nodeids = parse_nodeids(r.stdout)
        nodeids = _normalize_collected_nodeids(base, suite.path, nodeids)
        return [TestItem(id=n) for n in nodeids]

    def make_job_command(
        self,
        ctx: StageContext,
        suite_cfg: Any,
        plan: JobPlan,
        *,
        attempt: int,
        artifacts_root: str,
    ) -> ExecutableCommand:
        suite = suite_cfg
        if suite.kind != "pytest":
            raise TypeError(f"PytestRunnerAdapter requires suite.kind='pytest', got {suite.kind!r}")

        base = ctx.repo_root_in_env

        cmd: list[str] = list(_pytest_command_prefix(ctx))

        rootdir = _normalize_rootdir(base, suite.rootdir)
        if rootdir:
            cmd += [f"--rootdir={rootdir}"]

        if suite.markers:
            cmd += ["-m", suite.markers]

        if suite.args:
            cmd += suite.args.split()

        # deterministic outputs per job+attempt written to artifacts_root
        if ctx.outputs.junit:
            junit_name = _scoped_output_name(ctx.outputs.junit, plan.job_id, attempt)
            cmd += [f"--junitxml={artifacts_root}/{junit_name}"]
        if ctx.outputs.html:
            html_name = _scoped_output_name(ctx.outputs.html, plan.job_id, attempt)
            cmd += [f"--html={artifacts_root}/{html_name}", "--self-contained-html"]

        cmd += [item.id for item in plan.items]

        return ExecutableCommand(
            cmd=cmd,
            env=ctx.job_env(
                suite,
                job_id=plan.job_id,
                attempt=attempt,
                artifacts_root=artifacts_root,
            ),
            workdir=".",
            workdir_repo=True,
            artifacts_dir=None,                 # StageExecutor fills host mount path
            artifacts_root=artifacts_root,
            network=(ctx.docker.network if ctx.docker else None),
            shm_size=(ctx.docker.shm_size if ctx.docker else None),
            keep=bool(ctx.docker.keep_container) if ctx.docker else False,
            entrypoint="",
        )

    def _env(self, ctx: StageContext, suite_cfg: Any) -> dict[str, str]:
        return ctx.suite_env(suite_cfg)
