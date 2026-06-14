from __future__ import annotations

from dataclasses import dataclass

from testfabric.orchestrator.plan.models import StagePlan


@dataclass(frozen=True)
class LifecycleState:
    group_failed: bool = False
    group_open: bool = False


def before_stage(state: LifecycleState, stage: StagePlan) -> LifecycleState:
    if str(stage.lifecycle_mode or "run") == "setup":
        return LifecycleState(group_failed=False, group_open=True)
    if state.group_open:
        return state
    return LifecycleState(group_failed=state.group_failed, group_open=True)


def should_execute_stage(stage: StagePlan, *, state: LifecycleState) -> tuple[bool, str | None]:
    if not state.group_failed:
        return True, None
    if str(stage.lifecycle_when or "on_success") == "always":
        return True, None
    return False, "prior lifecycle group failed"


def after_stage(state: LifecycleState, stage: StagePlan, *, ok: bool) -> LifecycleState:
    next_failed = state.group_failed or (not ok)
    if str(stage.lifecycle_mode or "run") == "teardown":
        return LifecycleState(group_failed=False, group_open=False)
    return LifecycleState(group_failed=next_failed, group_open=state.group_open)
