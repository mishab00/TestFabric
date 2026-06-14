# TestFabric Library Core Refactor

## Goal

Make TestFabric usable as a lightweight execution and evidence toolkit for Python-first pytest infrastructure projects without requiring YAML scenarios, stages, workers, FastAPI, SQLAlchemy, Docker, or SSH.

TestFabric must stay domain-neutral. Consumer projects own their domain API, fixtures, runtime adapters, and checks. TestFabric provides reusable run context, artifact layout, evidence capture, command result modeling, redaction, and optional orchestration/server layers.

## Current Layering

Core/library candidates:

- `testfabric.runtime`: new library `RunContext`, `ArtifactLayout`, `LibraryRunRequest`.
- `testfabric.evidence`: new `EvidenceRecorder` and optional sinks.
- `testfabric.commands`: new `CommandResult`.
- `testfabric.redaction`: public `SecretRedactor`.
- `testfabric.core.events`: reusable low-level JSONL event sink, but still oriented to existing event shape.

Orchestration/YAML layer:

- `testfabric.spec`
- `testfabric.orchestrator`
- `testfabric.execution.suites`
- `testfabric.execution.executors`
- `testfabric.watch`
- `testfabric.workspace`
- `testfabric.cli`

Runtime-specific optional layers:

- Docker: `testfabric.execution.executors.docker`, `testfabric.health.probes_docker`, `testfabric.bootstrap.remote_docker`.
- SSH/remote workers: `testfabric.worker.remote`, `testfabric.worker.client`, `testfabric.workers`.
- Server/queue: `testfabric.api`, `testfabric.core.contracts`, `testfabric.history`.
- Pytest helpers: `testfabric.pytest`.

## New Library API

Recommended imports for consumer pytest projects:

```python
from testfabric.runtime import ArtifactLayout, LibraryRunRequest, RunContext
from testfabric.evidence import EvidenceRecorder
from testfabric.commands import CommandResult
from testfabric.redaction import SecretRedactor
```

The library path does not import YAML, Docker, Paramiko, FastAPI, SQLAlchemy, or pytest.

## Dependency Extras

Core install should remain small. Optional extras:

- `testfabric[pytest]`
- `testfabric[docker]`
- `testfabric[ssh]`
- `testfabric[server]`
- `testfabric[allure]`
- `testfabric[orchestration]`
- `testfabric[all]`

The CLI/YAML flow should be documented as requiring `testfabric[orchestration]` plus runtime-specific extras such as `docker` or `ssh`.

## Follow-Up Tasks

1. Rename server queue model `RunRequest` to `FabricRunRequest` or alias it with a deprecation path.
2. Update orchestrator construction to produce and carry the new library `RunContext` alongside the existing stage context.
3. Make `PathManager` consume `ArtifactLayout` directly, keeping stage/worker paths as an orchestration extension.
4. Teach dispatchers/executors to return `CommandResult`, while keeping `ExecResult` as a compatibility alias during migration.
5. Route orchestrator command logs through `EvidenceRecorder.record_command`.
6. Add optional pytest plugin hooks under the `pytest` extra without taking ownership of consumer fixtures.
7. Add an optional Allure sink package test that runs only when `allure` is installed.
8. Move API/store contracts under a server namespace after introducing compatibility imports.
