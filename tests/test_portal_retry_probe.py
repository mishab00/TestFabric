from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_retry_probe() -> None:
    attempt = os.environ.get("TESTFABRIC_ATTEMPT")
    artifacts_dir = os.environ.get("TESTFABRIC_JOB_ARTIFACTS_DIR")
    if attempt is None or artifacts_dir is None:
        pytest.skip("retry probe only runs inside a TestFabric job environment")
    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "attempt.txt").write_text(f"attempt={attempt}\n", encoding="utf-8")
    assert attempt != "0"
