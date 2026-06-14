from __future__ import annotations

from typing import Any

from testfabric.core.context import StageContext
from testfabric.core.envs import merge_env
from testfabric.execution.executors.base import ExecutableCommand
from testfabric.execution.suites.base import TestItem, JobPlan


def _step_argv(suite: Any) -> list[str]:
    cmd = list(getattr(suite, "cmd", []) or [])
    if cmd:
        return cmd

    bash = (getattr(suite, "bash", None) or "").strip()
    if bash:
        return ["bash", "-lc", bash]

    shell = (getattr(suite, "shell", None) or "").strip()
    if shell:
        return ["sh", "-lc", shell]

    return []


class ExpectRunnerAdapter:
    """
    Expect runner:
      - one interactive session per stage/job plan
      - command/bash/shell define the session process
      - prompt/response steps are handled by ExpectRuntime
    """

    name = "expect"

    def collect(self, ctx: StageContext, suite_cfg: Any, executor: Any) -> list[TestItem]:
        suite = suite_cfg
        if suite.kind != "expect":
            raise TypeError(f"ExpectRunnerAdapter requires suite.kind='expect', got {suite.kind!r}")
        return [TestItem(id="session")]

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
        if suite.kind != "expect":
            raise TypeError(f"ExpectRunnerAdapter requires suite.kind='expect', got {suite.kind!r}")

        cmd = _step_argv(suite)

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
