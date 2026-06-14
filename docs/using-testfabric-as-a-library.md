# Using TestFabric as a Library

TestFabric can be used directly from Python tests as an artifact and evidence toolkit. A consumer project keeps its own fixtures, domain objects, adapters, and checks. TestFabric only records what happened.

## Minimal Example

```python
from testfabric.evidence import EvidenceRecorder
from testfabric.runtime import LibraryRunRequest, RunContext
from testfabric.redaction import SecretRedactor

request = LibraryRunRequest(
    run_id="local-smoke",
    project="infra-project",
    suite="smoke",
    artifact_dir="artifacts",
    redaction_values=("XXXX",),
)
context = RunContext.from_request(
    request,
    redactor=SecretRedactor.from_values(
        request.redaction_values,
        field_names=("password", "token"),
        regex_patterns=(r"Bearer\s+[A-Za-z0-9._-]+",),
    ),
)
evidence = EvidenceRecorder(context)

with evidence.step("api.config_file_matches_runtime"):
    evidence.attach_json("api_config", {"token": "XXXX"})
    evidence.attach_text("status", "ready")

evidence.write_summary({"ok": True})
```

This creates:

```text
artifacts/runs/local-smoke/
  events.jsonl
  summary.json
  commands/
  checks/
  logs/
  traffic/
  allure-results/
```

## Pytest Fixture Wrapper

```python
import pytest

from testfabric.evidence import EvidenceRecorder
from testfabric.runtime import LibraryRunRequest, RunContext
from testfabric.redaction import SecretRedactor


@pytest.fixture
def evidence(request):
    run_request = LibraryRunRequest(
        run_id=request.node.name,
        project="consumer-project",
        suite="pytest",
        artifact_dir="artifacts",
        redaction_values=("XXXX",),
    )
    context = RunContext.from_request(
        run_request,
        env_prefix="CONSUMER",
        redactor=SecretRedactor.from_values(
            run_request.redaction_values,
            field_names=("password", "token"),
        ),
    )
    recorder = EvidenceRecorder(context)
    yield recorder
    recorder.write_summary({"test": request.node.nodeid})
```

Domain fixtures remain in the consumer project:

```python
def test_runtime_status(env, checks, evidence):
    with evidence.step("runtime.status"):
        status = checks.runtime_status(env)
        evidence.attach_json("status", status)
        assert status["ok"]
```

## Optional Allure

Allure is optional:

```python
from testfabric.evidence import AllureEvidenceSink, EvidenceRecorder

evidence = EvidenceRecorder(context, sinks=[AllureEvidenceSink()])
```

Install with `testfabric[allure]`.
