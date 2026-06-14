# testfabric/suites/registry.py
from __future__ import annotations

from typing import Dict

from testfabric.execution.suites.base import BaseRunner
from testfabric.execution.suites.pytest import PytestRunnerAdapter
from testfabric.execution.suites.command import CommandRunnerAdapter
from testfabric.execution.suites.expect import ExpectRunnerAdapter


class RunnerRegistry:
    """
    Strict registry: stage.runner must match one of the known adapters.
    (No dynamic plugin loading yet; we can add later.)
    """

    def __init__(self) -> None:
        self._runners: Dict[str, BaseRunner] = {
            "pytest": PytestRunnerAdapter(),
            "command": CommandRunnerAdapter(),
            "expect": ExpectRunnerAdapter(),
        }

    def get(self, name: str) -> BaseRunner:
        key = (name or "").strip()
        if not key:
            raise ValueError("runner name is required")
        if key not in self._runners:
            raise ValueError(f"Unknown runner '{key}'. Available: {sorted(self._runners.keys())}")
        return self._runners[key]
