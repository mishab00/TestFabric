from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from testfabric.commands import CommandResult
from testfabric.evidence import EvidenceRecorder
from testfabric.redaction import SecretRedactor
from testfabric.runtime import ArtifactLayout, LibraryRunRequest, RunContext


class LibraryCoreTests(unittest.TestCase):
    def test_artifact_layout_uses_env_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = __import__("os").environ.get("DOMAIN_ARTIFACT_DIR")
            __import__("os").environ["DOMAIN_ARTIFACT_DIR"] = str(root / "domain-artifacts")
            try:
                layout = ArtifactLayout.for_run("run-001", env_prefix="DOMAIN").ensure()
            finally:
                if old is None:
                    __import__("os").environ.pop("DOMAIN_ARTIFACT_DIR", None)
                else:
                    __import__("os").environ["DOMAIN_ARTIFACT_DIR"] = old

            self.assertEqual(layout.run_dir, (root / "domain-artifacts" / "runs" / "run-001").resolve())
            self.assertTrue(layout.commands_dir.exists())
            self.assertTrue(layout.checks_dir.exists())
            self.assertTrue(layout.events_path.exists())

    def test_redactor_handles_values_fields_regex_and_nested_data(self) -> None:
        redactor = SecretRedactor.from_values(
            ("XXXX",),
            field_names=("password", "api-key"),
            regex_patterns=(r"Bearer\s+[A-Za-z0-9._-]+",),
        )
        data = {
            "password": "XXXX",
            "nested": {
                "api-key": "abc",
                "message": "token=XXXX Authorization: Bearer abc.def",
            },
        }

        redacted = redactor.redact_data(data)

        self.assertEqual(redacted["password"], "***")
        self.assertEqual(redacted["nested"]["api-key"], "***")
        self.assertIn("***", redacted["nested"]["message"])
        self.assertNotIn("XXXX", json.dumps(redacted))
        self.assertNotIn("Bearer abc.def", json.dumps(redacted))

    def test_evidence_recorder_writes_redacted_artifacts_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            request = LibraryRunRequest(
                run_id="run-001",
                project="project",
                suite="suite",
                artifact_dir=td,
                redaction_values=("XXXX",),
            )
            context = RunContext.from_request(
                request,
                redactor=SecretRedactor.from_values(request.redaction_values, field_names=("token",)),
            )
            recorder = EvidenceRecorder(context)

            with recorder.step("check.secret"):
                json_path = recorder.attach_json("payload", {"token": "XXXX", "value": "XXXX"})
                text_path = recorder.attach_text("status", "XXXX is hidden")

            events = context.layout.events_path.read_text(encoding="utf-8")
            self.assertIn('"kind": "step"', events)
            self.assertIn('"kind": "attachment"', events)
            self.assertTrue(context.layout.run_request_path.exists())
            self.assertTrue(context.layout.runtime_config_path.exists())
            self.assertNotIn("XXXX", events)
            self.assertNotIn("XXXX", json_path.read_text(encoding="utf-8"))
            self.assertNotIn("XXXX", text_path.read_text(encoding="utf-8"))

    def test_command_result_records_transcript_without_raising_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            layout = ArtifactLayout.for_run("run-001", artifact_dir=td).ensure()
            recorder = EvidenceRecorder(layout, redactor=SecretRedactor.from_values(("XXXX",)))
            result = CommandResult(
                command=["sh", "-c", "exit 7"],
                exit_code=7,
                stdout="XXXX\n",
                stderr="failed\n",
                duration_ms=12,
                host="local",
                transport="local",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

            metadata_path = recorder.record_command("failing-command", result)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertFalse(result.ok)
            self.assertEqual(metadata["exit_code"], 7)
            self.assertEqual(metadata["command"], "sh -c 'exit 7'")
            self.assertNotIn("XXXX", (metadata_path.parent / "stdout.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
