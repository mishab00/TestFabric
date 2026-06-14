from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


class ReportingContractTests(unittest.TestCase):
    def test_summary_and_events_include_target_and_transport_metadata(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_minimal_artifact_local.yaml"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec.run.repo_url = str(repo_root)
            spec.run.workdir = str(root / "work")
            spec.run.artifacts_dir = str(root / "artifacts")

            run_id = "reporting-contract"
            result = Orchestrator(
                spec,
                RunOptions.from_spec_and_cli(spec, run_id=run_id),
            ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))

            run_dir = Path(result["run_dir"])
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            stage_summary = json.loads(
                (
                    run_dir
                    / "workers"
                    / "w000"
                    / "stages"
                    / "01-write_artifact"
                    / "stage-summary.json"
                ).read_text(encoding="utf-8")
            )

            self.assertEqual(summary["run"]["run_id"], run_id)
            self.assertIsNotNone(summary["run"]["started_at"])
            self.assertIsNotNone(summary["run"]["ended_at"])
            self.assertEqual(summary["targets"][0]["target_id"], "local")
            self.assertEqual(summary["targets"][0]["name"], "local")
            self.assertEqual(summary["targets"][0]["kind"], "local")

            stage = summary["stages"][0]
            self.assertEqual(stage, stage_summary)
            self.assertIsNotNone(stage["started_at"])
            self.assertIsNotNone(stage["ended_at"])
            self.assertNotIn("ok", stage)
            self.assertNotIn("reports_dir", stage["artifacts"])
            self.assertNotIn("reports_path", stage["artifacts"])
            self.assertEqual(stage["debug"]["stage_reports_path"], "workers/w000/stages/01-write_artifact/reports")
            self.assertNotIn("stage_reports_dir", stage["debug"])
            self.assertEqual(stage["target_id"], "local")
            self.assertEqual(stage["target_name"], "local")
            self.assertEqual(stage["target_kind"], "local")
            self.assertEqual(stage["transport"]["type"], "local")
            self.assertEqual(stage["transport"]["worker_id"], "w000")

            job = stage["jobs"][0]
            self.assertIsNotNone(job["started_at"])
            self.assertIsNotNone(job["ended_at"])
            self.assertTrue(job["was_started"])
            self.assertEqual(job["target_id"], "local")
            self.assertEqual(job["transport"]["type"], "local")
            self.assertEqual(job["transport"]["worker_id"], "w000")
            self.assertTrue(any(item["label"].startswith("Artifact:") for item in stage["artifacts"]["highlights"]))

            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            event_table = summary["events"]
            self.assertEqual(event_table["path"], "events.jsonl")
            self.assertEqual(event_table["total"], len(events))
            self.assertEqual(event_table["counts_by_component"]["run"], 2)
            self.assertGreaterEqual(event_table["counts_by_component"]["stage"], 1)
            self.assertTrue(any(row["component"] == "job" and row["action"] == "end" for row in event_table["rows"]))
            self.assertTrue(all("message" in row for row in event_table["rows"]))
            self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
            self.assertTrue(all(event["run_id"] == run_id for event in events))
            self.assertTrue(all("message" in event for event in events))

            stage_start = next(
                event for event in events if event["component"] == "stage" and event["action"] == "start"
            )
            self.assertEqual(stage_start["target_id"], "local")
            self.assertEqual(stage_start["stage_id"], "01-write_artifact")

            job_end = next(
                event
                for event in events
                if event["component"] == "job" and event["action"] == "end" and event["job_id"] == "job-0001"
            )
            self.assertEqual(job_end["target_id"], "local")
            self.assertEqual(job_end["stage_id"], "01-write_artifact")
            self.assertEqual(job_end["attempt"], 0)


if __name__ == "__main__":
    unittest.main()
