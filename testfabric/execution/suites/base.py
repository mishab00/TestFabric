# testfabric/suites/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any

from testfabric.core.context import StageContext


@dataclass(frozen=True)
class TestItem:
    """
    Normalized unit of work.
    - pytest: nodeid
    - command: step name
    """
    id: str


@dataclass(frozen=True)
class JobPlan:
    job_id: str
    items: list[TestItem]


class BaseRunner(Protocol):
    """
    Runner = domain-level test semantics.
    It does NOT execute by itself; it produces commands for an executor.
    """
    name: str

    def collect(self, ctx: StageContext, suite_cfg: Any, executor: Any) -> list[TestItem]: ...

    def make_job_command(
        self,
        ctx: StageContext,
        suite_cfg: Any,
        plan: JobPlan,
        *,
        attempt: int,
        artifacts_root: str,
    ) -> Any: ...
