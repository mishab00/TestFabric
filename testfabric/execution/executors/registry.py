# testfabric/execution/executors/registry.py
from __future__ import annotations

from typing import Dict

from testfabric.execution.executors.base import ExecutorAdapter
from testfabric.execution.executors.local import LocalExecutorAdapter
from testfabric.execution.executors.docker import DockerExecutorAdapter


class ExecutorRegistry:
    """
    Strict registry (no plugin loading yet).
    """

    def __init__(self) -> None:
        self._executors: Dict[str, ExecutorAdapter] = {
            "local": LocalExecutorAdapter(),
            "docker": DockerExecutorAdapter(),
        }

    def get(self, name: str) -> ExecutorAdapter:
        key = (name or "").strip()
        if not key:
            raise ValueError("executor name is required")
        if key not in self._executors:
            raise ValueError(f"Unknown executor '{key}'. Available: {sorted(self._executors.keys())}")
        return self._executors[key]
