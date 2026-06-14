# testfabric/execution/suites/command.py
from __future__ import annotations

from dataclasses import dataclass
import shlex
import re
from typing import Any

from testfabric.core.context import StageContext
from testfabric.core.envs import merge_env
from testfabric.execution.executors.base import ExecutableCommand
from testfabric.execution.suites.base import TestItem, JobPlan


@dataclass(frozen=True)
class CommandItem(TestItem):
    step_name: str


def _step_name(s: Any, i: int) -> str:
    nm = (getattr(s, "name", None) or "").strip()
    return nm if nm else f"step-{i+1:03d}"


def _shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(str(x)) for x in argv)


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _step_argv(step: Any) -> list[str]:
    cmd = list(getattr(step, "cmd", []) or [])
    if cmd:
        return cmd

    bash = (getattr(step, "bash", None) or "").strip()
    if bash:
        return ["bash", "-lc", bash]

    shell = (getattr(step, "shell", None) or "").strip()
    if shell:
        return ["sh", "-lc", shell]

    return []


def _suite_argv(suite: Any) -> list[str]:
    return _step_argv(suite)


def _capture_relpath(step: Any, attr: str) -> str:
    return (getattr(step, attr, None) or "").strip()


def _double_quote_expandable(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")


class CommandRunnerAdapter:
    """
    Command runner:
      - collect -> suite.steps or suite.dry_steps => CommandItem list
      - make_job_command -> bash -lc script that runs picked steps in order

    Workdir semantics:
      - docker: repo mounted at ctx.repo_root_in_env (usually /work)
      - local:  executor resolves '.' relative to ctx.repo_path (workdir_repo=True)
    """

    name = "command"

    def collect(self, ctx: StageContext, suite_cfg: Any, executor: Any) -> list[TestItem]:
        suite = suite_cfg
        if suite.kind != "command":
            raise TypeError(f"CommandRunnerAdapter requires suite.kind='command', got {suite.kind!r}")

        if list(getattr(suite, "expect", []) or []):
            return [CommandItem(id="session", step_name="session")]

        steps = suite.dry_steps if ctx.run.mode == "dry-run" else suite.steps

        items: list[TestItem] = []
        for i, s in enumerate(steps):
            nm = _step_name(s, i)
            items.append(CommandItem(id=nm, step_name=nm))
        return items

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
        if suite.kind != "command":
            raise TypeError(f"CommandRunnerAdapter requires suite.kind='command', got {suite.kind!r}")

        if list(getattr(suite, "expect", []) or []):
            cmd = _suite_argv(suite)
            if not cmd:
                raise ValueError("interactive command suite must define cmd, bash, or shell")

            env = merge_env(
                ctx.base_env,
                dict(getattr(suite, "env", {}) or {}),
            )
            env = merge_env(
                env,
                ctx.artifact_env(
                    job_id=plan.job_id,
                    attempt=attempt,
                    artifacts_root=artifacts_root or "/artifacts",
                ),
            )

            workdir = (getattr(suite, "workdir", ".") or ".").strip() or "."
            if (ctx.executor or "").strip().lower() == "docker":
                base_workdir = ctx.repo_root_in_env
                if workdir == ".":
                    workdir = base_workdir
                elif not workdir.startswith("/"):
                    workdir = f"{base_workdir.rstrip('/')}/{workdir.lstrip('./')}"

            return ExecutableCommand(
                cmd=cmd,
                env=env,
                workdir=workdir,
                workdir_repo=True,
                artifacts_dir=None,
                artifacts_root=artifacts_root or "/artifacts",
                network=None,
                shm_size=None,
                keep=False,
                entrypoint="",
            )

        steps = suite.dry_steps if ctx.run.mode == "dry-run" else suite.steps
        wanted = {it.id for it in plan.items}

        picked: list[Any] = []
        for i, s in enumerate(steps):
            nm = _step_name(s, i)
            if nm in wanted:
                picked.append(s)

        exec_kind = (ctx.executor or "").strip().lower()
        if exec_kind == "docker":
            base_workdir = ctx.repo_root_in_env  # usually /work
            workdir_repo = True
        else:
            base_workdir = "."
            workdir_repo = True

        lines: list[str] = [
            "set -u",
            "set -o pipefail",
            f'BASE="{base_workdir}"',
            f'ART="{(artifacts_root or "")}"',
            "failed_hard=0",
            "failed_soft=0",
            'if [ -n "$ART" ]; then mkdir -p "$ART"; fi',
            'echo "=== COMMAND JOB START ==="',
            'echo "base_workdir=$BASE"',
            'echo "artifacts_root=$ART"',
            "",
        ]

        for idx, s in enumerate(picked):
            name = _step_name(s, idx)

            step_workdir = (getattr(s, "workdir", "") or ".").strip()
            env = dict(getattr(s, "env", {}) or {})
            cmd = _step_argv(s)
            stdout_to = _capture_relpath(s, "stdout_to")
            stderr_to = _capture_relpath(s, "stderr_to")

            continue_on_fail = bool(getattr(s, "continue_on_fail", False))
            always = bool(getattr(s, "always", False))

            skip_when_failed_hard = "0" if always else "1"

            lines += [
                f'echo "=== STEP: {name} ==="',
                f'echo "workdir={step_workdir} continue_on_fail={str(continue_on_fail).lower()} always={str(always).lower()}"',
                "run_step=1",
                f'if [ "$failed_hard" -ne 0 ] && [ "{skip_when_failed_hard}" -eq 1 ]; then run_step=0; fi',
                f'if [ "$run_step" -eq 0 ]; then echo "SKIPPED STEP (prior hard failure): {name}"; else',
                '  cd "$BASE" || exit 2',
                f'  cd "{step_workdir}" || exit 2',
            ]

            for kenv, venv in env.items():
                if not _ENV_KEY_RE.match(str(kenv)):
                    raise ValueError(f"Invalid env var key in command step: {kenv!r}")
                vv = str(venv).replace('"', '\\"')
                lines.append(f'  export {kenv}="{vv}"')

            if stdout_to:
                lines.append(f'  stdout_target="$ART"/"{_double_quote_expandable(stdout_to)}"')
                lines.append('  mkdir -p "$(dirname "$stdout_target")"')
            if stderr_to:
                lines.append(f'  stderr_target="$ART"/"{_double_quote_expandable(stderr_to)}"')
                lines.append('  mkdir -p "$(dirname "$stderr_target")"')

            if cmd:
                pretty = _shell_join(cmd)
                redirs: list[str] = []
                if stdout_to:
                    redirs.append('>"$stdout_target"')
                if stderr_to:
                    redirs.append('2>"$stderr_target"')
                redir_suffix = f" {' '.join(redirs)}" if redirs else ""
                lines += [
                    f'  echo "+ {pretty}"',
                    f"  ({pretty}){redir_suffix}",
                    "  rc=$?",
                    "  if [ $rc -ne 0 ]; then",
                    '    echo "STEP FAILED: rc=$rc"',
                    ("    failed_soft=1" if continue_on_fail else "    failed_hard=1"),
                    "  fi",
                ]
            else:
                lines += ['  echo "WARNING: empty cmd"']

            lines += ["fi", ""]

        lines += [
            'echo "=== COMMAND JOB END ==="',
            'echo "failed_hard=$failed_hard failed_soft=$failed_soft"',
            'if [ "$failed_hard" -ne 0 ]; then exit 1; fi',
            "exit 0",
        ]

        script = "\n".join(lines) + "\n"

        return ExecutableCommand(
            cmd=["bash", "-lc", script],
            env=ctx.job_env(
                suite_cfg,
                job_id=plan.job_id,
                attempt=attempt,
                artifacts_root=artifacts_root or "/artifacts",
            ),
            workdir=base_workdir,
            workdir_repo=workdir_repo,
            artifacts_dir=None,         # dispatcher/stage executor will fill host mount for docker
            artifacts_root=artifacts_root or "/artifacts",
            network=None,
            shm_size=None,
            keep=False,
            entrypoint="",
        )
