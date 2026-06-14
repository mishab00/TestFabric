# testfabric.worker — Remote worker runtime & client
#
# Micro-package: testfabric[ssh]
# Dependencies: testfabric.core, paramiko (optional)
#
# Provides:
#   - WorkerRuntime: local worker execution runtime
#   - RemoteWorkerRuntime: remote worker that polls an API server for work
#   - WorkerClient: HTTP client for the worker API
#   - WorkerServeLoop & WorkerServeSummary: serve loop management
#
# Usage:
#   from testfabric.worker import WorkerClient, RemoteWorkerRuntime

from .runtime import WorkerRuntime
from .client import WorkerClient
from .remote import RemoteWorkerRuntime
from .serve import WorkerServeLoop, WorkerServeSummary

__all__ = [
    "RemoteWorkerRuntime",
    "WorkerClient",
    "WorkerRuntime",
    "WorkerServeLoop",
    "WorkerServeSummary",
]
