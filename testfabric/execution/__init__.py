# testfabric.execution — Local & Docker task execution
#
# Micro-package: testfabric[execution]
# Extras needed: testfabric[docker] for Docker executor
#
# Provides:
#   - ExecutorAdapter protocol and ExecutorRegistry
#   - LocalExecutorAdapter (run commands on the controller)
#   - DockerExecutorAdapter (run commands in Docker containers)
#   - BaseRunner protocol and RunnerRegistry
#   - PytestRunnerAdapter, CommandRunnerAdapter, ExpectRunnerAdapter
#   - Shared data types: ExecutableCommand, ExecResult, BuildRequest, TestItem, JobPlan

from testfabric.execution.executors.base import (
    BuildRequest,
    ExecResult,
    ExecutableCommand,
    ExecutorAdapter,
)
from testfabric.execution.executors.local import LocalExecutorAdapter
from testfabric.execution.executors.registry import ExecutorRegistry
from testfabric.execution.suites.base import BaseRunner, JobPlan, TestItem
from testfabric.execution.suites.registry import RunnerRegistry

__all__ = [
    # Executor layer
    "BuildRequest",
    "ExecResult",
    "ExecutableCommand",
    "ExecutorAdapter",
    "ExecutorRegistry",
    "LocalExecutorAdapter",
    # Runner layer
    "BaseRunner",
    "JobPlan",
    "RunnerRegistry",
    "TestItem",
]

