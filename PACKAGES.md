# TestFabric Micro-Package Architecture

TestFabric is designed as a **monorepo of composable micro-packages**. Each
subpackage is a self-contained capability with well-defined boundaries,
so you can import and reuse only what you need in your own projects.

---

## Quick Install

```bash
# Core only (zero external dependencies)
pip install testfabric

# Pick the capabilities you need:
pip install testfabric[parallel]        # parallel task dispatch
pip install testfabric[docker]          # Docker-wrapped execution
pip install testfabric[ssh]             # remote host execution
pip install testfabric[watch]           # reactive watchers
pip install testfabric[yaml]            # YAML spec/schema engine
pip install testfabric[reporting]       # stage/run reporting
pip install testfabric[orchestration]   # full YAML-driven pipeline
pip install testfabric[server]          # API server (FastAPI)
pip install testfabric[pytest]          # pytest integration
pip install testfabric[allure]          # Allure evidence reporter
pip install testfabric[all]             # everything
```

---

## Dependency Graph

```
                        ┌─────────┐
                        │  core   │  ← zero-dep foundation
                        └────┬────┘
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌───────────┐ ┌───────────┐  ┌──────────┐
        │ execution │ │  workers  │  │  watch   │
        │ (local)   │ │  (pool)   │  │          │
        └─────┬─────┘ └─────┬─────┘  └──────────┘
              │              │
              ▼              ▼
        ┌───────────┐ ┌───────────┐
        │  docker   │ │ parallel  │
        │ executor  │ │ dispatch  │
        └─────┬─────┘ └─────┬─────┘
              │              │
              └──────┬───────┘
                     ▼
           ┌──────────────────┐
           │  orchestration   │◄──── yaml (spec/schema)
           │  (full pipeline) │
           └────────┬─────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
     ┌─────────┐ ┌──────┐ ┌──────┐
     │reporting│ │  ssh  │ │server│
     └─────────┘ └──────┘ └──────┘
```

---

## Micro-Package Catalog

### 1. `testfabric.core` — Foundation Layer
> **Extra:** none (always available) · **External deps:** none

Events, contracts, redaction, environment helpers, serialization, constants.

```python
from testfabric.core import (
    Event, EventSink, NullEvents, FileEvents,  # event system
    RunRequest, RunResult, RunStatus,           # API contracts
    SecretRedactor, redact_text,                # redaction
    merge_env, expand_vars,                     # env helpers
    to_dict_like,                               # serialization
)
```

**Use when you need:** a foundation for custom task orchestration, event-driven
logging, secret redaction, or building your own execution engine.

---

### 2. `testfabric.execution` — Task Execution
> **Extra:** none (local) / `docker` (Docker) · **External deps:** `docker` (optional)

Executor protocol, local process runner, Docker executor, runner registry.

```python
from testfabric.execution import (
    ExecutableCommand, ExecResult,        # command data types
    ExecutorAdapter,                       # protocol
    LocalExecutorAdapter,                  # run on host
    ExecutorRegistry,                      # registry
    BaseRunner, TestItem, JobPlan,         # runner protocol
    RunnerRegistry,                        # runner registry
)

# Run a command locally
executor = LocalExecutorAdapter()
result = executor.run(ctx, ExecutableCommand(cmd=["echo", "hello"]))

# Run inside Docker
from testfabric.execution.executors.docker import DockerExecutorAdapter
docker_exec = DockerExecutorAdapter()
```

**Use when you need:** to execute commands/tasks either locally or in Docker containers.

---

### 3. `testfabric.workers` — Worker Pool & Targets
> **Extra:** `parallel` · **External deps:** `pyyaml` (optional, for target files)

Worker pool allocation, target inventory loading (Ansible-style YAML).

```python
from testfabric.workers import WorkerPool, Worker

pool = WorkerPool(mode="local", capacity=4)
workers = pool.allocate(2)  # get 2 workers
w = pool.acquire()          # exclusive lease
pool.release(w)

# Target management
from testfabric.workers.targets import load_targets_file, select_targets
targets = load_targets_file("inventory.yaml")
web_hosts = select_targets(targets, "group:web")
```

**Use when you need:** a pool of execution slots with health-aware allocation,
or to load host inventories for multi-target execution.

---

### 4. `testfabric.watch` — Reactive Watchers
> **Extra:** `watch` · **External deps:** none

File, HTTP, metric, and remote-file watchers with pattern matching and actions.

```python
from testfabric.watch import WatchRuntime, WatchAbort
from testfabric.core import NullEvents

watch = WatchRuntime(
    {
        "sources": [
            {"name": "logs", "type": "file", "path": "/var/log/app.log"},
            {"name": "health", "type": "http", "url": "http://localhost:8080/health"},
        ],
        "watchers": [
            {
                "name": "error-check",
                "source": "logs",
                "when": {"match": "ERROR"},
                "then": {"fail": True},
            },
            {
                "name": "health-ok",
                "source": "health",
                "when": {"status": [200]},
                "then": {"report": {"message": "Service healthy"}},
            },
        ],
    },
    events=NullEvents(),
)
watch.start(phase="run")
# ... your task runs ...
watch.feed_output("some stdout text", stream="stdout")
watch.stop()

if watch.abort_requested:
    raise WatchAbort(watch.failure_reason)
```

**Use when you need:** real-time monitoring of files, HTTP endpoints, or shell
metrics during task execution, with automated reactions.

---

### 5. `testfabric.spec` — YAML Spec Engine
> **Extra:** `yaml` · **External deps:** `pydantic`, `pyyaml`

Full YAML specification schema with validation, profiles, inputs, and parametrization.

```python
from testfabric.spec import RunSpec

# Load and validate a YAML spec
spec = RunSpec.load("run.yaml", profile="ci")

# Load with input overrides
spec, resolved = RunSpec.load_resolved(
    "run.yaml",
    profile="staging",
    inputs_map={"api_key": "XXXX"},
)

# Lint a raw spec
from testfabric.spec import lint_raw_spec
for issue in lint_raw_spec(raw_dict):
    print(f"[{issue.level}] {issue.path}: {issue.message}")
```

**Use when you need:** the YAML-driven configuration experience for defining
pipelines, suites, targets, and execution parameters.

---

### 6. `testfabric.reporting` — Reporting & Analysis
> **Extra:** `reporting` · **External deps:** `pydantic`

Stage-level summaries, run-level aggregation, artifact collection, failure analysis.

```python
from testfabric.reporting import StageReporter, RunAggregator

reporter = StageReporter()
reporter.ensure_dirs(ctx)
payload = reporter.write_stage_summary(
    ctx,
    items_total=50,
    jobs_total=5,
    dispatch=dispatch_result,
)

aggregator = RunAggregator()
aggregator.write_run_summary(
    spec=spec,
    paths=paths,
    run_id="my-run",
    ok=True,
    stage_results=stage_results,
)
```

**Use when you need:** structured test result reporting, artifact inventory,
failure classification, and multi-target result aggregation.

---

### 7. `testfabric.orchestrator` — Full Pipeline Orchestration
> **Extra:** `orchestration` · **External deps:** `pydantic`, `pyyaml`, `typer`, `gitpython`

End-to-end YAML-driven run engine: plan → health check → stage execution → report.

```python
from testfabric.orchestrator import Orchestrator, RunOptions
from testfabric.spec import RunSpec

spec = RunSpec.load("run.yaml")
opts = RunOptions.from_spec_and_cli(spec, mode="run")
result = Orchestrator(spec, opts).run()
# result = {"ok": True, "run_id": "...", "stages": [...]}
```

The `LocalDispatcher` drives parallel job execution with retry:

```python
from testfabric.orchestrator import LocalDispatcher

dispatcher = LocalDispatcher()
dispatch_result = dispatcher.run_jobs(
    ctx=stage_ctx,
    plan=job_plans,
    make_cmd=my_command_factory,
    executor_adapter=local_executor,
    watch=watch_config,
)
```

**Use when you need:** the complete YAML-driven pipeline with multi-stage
execution, lifecycle hooks (setup/teardown), health preflight, and parallel dispatch.

---

### 8. `testfabric.evidence` — Evidence Recording
> **Extra:** none · **External deps:** none (optional: `allure-pytest`)

Structured evidence collection for library/programmatic usage.

```python
from testfabric.evidence import EvidenceRecorder
from testfabric.runtime import RunContext, LibraryRunRequest

ctx = RunContext.from_request(LibraryRunRequest(project="my-project"))
recorder = EvidenceRecorder(ctx)

with recorder.step("deploy"):
    recorder.attach_json("config", {"env": "prod"})
    # ... do work ...

recorder.write_summary({"verdict": "PASSED"})
```

---

### 9. Remote Execution

#### `testfabric[docker]` — Docker Execution
```python
from testfabric.execution.executors.docker import DockerExecutorAdapter
from testfabric.execution.executors.dockerops import build_image, docker_run
```

#### `testfabric[ssh]` — SSH Remote Execution
```python
from testfabric.worker import RemoteWorkerRuntime, WorkerClient
from testfabric.bootstrap.remote_docker import RemoteDockerBootstrap
```

---

### 10. `testfabric[server]` — API Server
> **External deps:** `fastapi`, `httpx`, `uvicorn`, `sqlalchemy`, `pydantic`

```python
from testfabric.api.app import create_app
app = create_app()
```

---

### 11. `testfabric[cli]` — Command-Line Interface

> Installed with `testfabric[orchestration]`

```bash
testfabric run spec.yaml --mode run --build
testfabric runs --team backend --status failed
testfabric show <run-id>
testfabric worker serve --worker-id w1
```

---

## Capability Matrix

| Capability                            | Import path                          | Extra needed      |
|---------------------------------------|--------------------------------------|-------------------|
| Run parallel tasks                    | `testfabric.orchestrator`            | `parallel`        |
| Run tasks in wrapped Docker           | `testfabric.execution.executors.docker` | `docker`       |
| Run tasks on remote hosts             | `testfabric.worker`                  | `ssh`             |
| Analyze and report                    | `testfabric.reporting`               | `reporting`       |
| Parallel execution of various tasks   | `testfabric.orchestrator.dispatch`   | `parallel`        |
| Reactive watchers                     | `testfabric.watch`                   | `watch`           |
| YAML-driven specs                     | `testfabric.spec`                    | `yaml`            |
| Health checks                         | `testfabric.health`                  | (none)            |
| Evidence recording                    | `testfabric.evidence`                | (none)            |
| Worker pool management                | `testfabric.workers`                 | (none)            |
| Target/inventory management           | `testfabric.workers.targets`         | `yaml`            |
| Secret redaction                      | `testfabric.core`                    | (none)            |
| Full YAML pipeline orchestration      | `testfabric.orchestrator`            | `orchestration`   |
| API server                            | `testfabric.api`                     | `server`          |
| CLI                                   | `testfabric.cli`                     | `orchestration`   |

---

## Boundary Rules

1. **`testfabric.core`** depends on **nothing** (stdlib only)
2. **`testfabric.execution`** depends on `core` only
3. **`testfabric.workers`** depends on `core` only (+ pyyaml for target files)
4. **`testfabric.watch`** depends on `core` only
5. **`testfabric.reporting`** depends on `core` + `artifacts`
6. **`testfabric.spec`** depends on `pydantic` + `pyyaml` (+ `inputs`, `workers.targets`)
7. **`testfabric.orchestrator`** depends on nearly everything — it is the **composition root**
8. **`testfabric.server`** depends on `core` + `fastapi/sqlalchemy`
9. **`testfabric.cli`** depends on `orchestrator` + `typer`

This layering means you can, for example:
- Use `testfabric.watch` + `testfabric.execution` to build a custom monitoring pipeline
- Use `testfabric.workers` + `testfabric.execution` for ad-hoc parallel job dispatch
- Use `testfabric.reporting` alone to generate structured test summaries
- Use `testfabric.spec` to parse YAML specs without any execution logic

---

## Future: Extracting to Separate PyPI Packages

When a micro-package becomes large enough to warrant its own release cycle,
extract it to its own repo and replace the subpackage with a thin re-export:

```python
# testfabric/watch/__init__.py (after extraction)
from testfabric_watch import WatchRuntime, WatchAbort  # type: ignore
__all__ = ["WatchAbort", "WatchRuntime"]
```

The rest of the codebase sees no change because imports stay the same.

