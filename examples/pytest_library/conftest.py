from __future__ import annotations

import pytest

from testfabric.evidence import EvidenceRecorder
from testfabric.runtime import LibraryRunRequest, RunContext
from testfabric.redaction import SecretRedactor


@pytest.fixture
def evidence(request):
    run_request = LibraryRunRequest(
        run_id=request.node.name,
        project="domain-project",
        suite="pytest",
        artifact_dir="artifacts",
        redaction_values=("XXXX",),
    )
    context = RunContext.from_request(
        run_request,
        env_prefix="DOMAIN",
        redactor=SecretRedactor.from_values(
            run_request.redaction_values,
            field_names=("password", "token"),
            regex_patterns=(r"Bearer\s+[A-Za-z0-9._-]+",),
        ),
    )
    recorder = EvidenceRecorder(context)
    yield recorder
    recorder.write_summary({"nodeid": request.node.nodeid})
