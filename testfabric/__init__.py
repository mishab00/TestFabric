# testfabric — Distributed execution fabric for test and task workloads
#
# ┌──────────────────────────────────────────────────────────────────────┐
# │                     MICRO-PACKAGE ARCHITECTURE                      │
# ├──────────────────────────────────────────────────────────────────────┤
# │                                                                     │
# │  pip install testfabric                  → core only (zero deps)   │
# │  pip install testfabric[yaml]            → + YAML spec/schema      │
# │  pip install testfabric[docker]          → + Docker execution      │
# │  pip install testfabric[ssh]             → + SSH / remote exec     │
# │  pip install testfabric[parallel]        → + parallel dispatch     │
# │  pip install testfabric[watch]           → + reactive watchers     │
# │  pip install testfabric[reporting]       → + stage/run reporting   │
# │  pip install testfabric[orchestration]   → full YAML orchestrator  │
# │  pip install testfabric[server]          → API server (FastAPI)    │
# │  pip install testfabric[pytest]          → pytest integration      │
# │  pip install testfabric[allure]          → Allure reporting        │
# │  pip install testfabric[all]             → everything              │
# │                                                                     │
# │  Dependency graph (→ means "depends on"):                          │
# │                                                                     │
# │  core ← execution ← parallel ← orchestration → yaml               │
# │    ↑         ↑                       ↑                              │
# │  watch    docker                   reporting                       │
# │    ↑                                                                │
# │  server ← orchestration → ssh                                     │
# │                                                                     │
# └──────────────────────────────────────────────────────────────────────┘
#
# Each subpackage can be imported independently:
#
#   from testfabric.core import Event, EventSink, NullEvents
#   from testfabric.execution import ExecutableCommand, LocalExecutorAdapter
#   from testfabric.watch import WatchRuntime
#   from testfabric.workers import WorkerPool
#   from testfabric.reporting import StageReporter
#   from testfabric.orchestrator import Orchestrator, RunOptions
#   from testfabric.spec import RunSpec

