from __future__ import annotations

import unittest

from pydantic import ValidationError

from testfabric.core import (
    CompareRequest,
    RunLease,
    ReplayRequest,
    RunQuery,
    RunRequest,
    RunResponse,
    RunResult,
    RunStatus,
    WorkerRegistration,
    WorkerStatus,
)


class CoreContractTests(unittest.TestCase):
    def test_run_request_round_trips_and_keeps_submission_metadata(self) -> None:
        request = RunRequest(
            run_id="run-123",
            mode="dry-run",
            priority=7,
            routing_labels=["linux", "docker"],
            spec_yaml="run:\n  name: sample\n",
            repo_url="git@example.com:org/repo.git",
            ref="main",
            team="platform",
            project="api-gateway",
            workspace="/workspaces/api-gateway",
            context="team-staging",
            inputs={"env": "staging", "retries": 2},
            tags=["nightly", "docker"],
            metadata={"triggered_by": "alice"},
        )

        dumped = request.model_dump(exclude_none=True)
        restored = RunRequest.model_validate(dumped)

        self.assertEqual(restored, request)
        self.assertEqual(restored.mode, "dry-run")
        self.assertEqual(restored.priority, 7)
        self.assertEqual(restored.routing_labels, ["linux", "docker"])
        self.assertEqual(restored.inputs["retries"], 2)
        self.assertEqual(restored.metadata["triggered_by"], "alice")

    def test_run_response_status_and_links_round_trip(self) -> None:
        status = RunStatus(
            run_id="run-123",
            state="running",
            verdict=None,
            stage_id="stage-1",
            job_id="job-1",
            attempt=0,
            progress={"stages_done": 1, "stages_total": 3},
        )
        response = RunResponse(
            run_id="run-123",
            accepted=True,
            status=status,
            message="queued for execution",
            links={"self": "/runs/run-123", "events": "/runs/run-123/events"},
        )

        dumped = response.model_dump(exclude_none=True)
        restored = RunResponse.model_validate(dumped)

        self.assertEqual(restored, response)
        self.assertEqual(restored.status.state, "running")
        self.assertEqual(restored.links["self"], "/runs/run-123")

    def test_run_result_query_compare_and_replay_contracts_are_serializable(self) -> None:
        request = RunRequest(
            run_id="run-123",
            mode="run",
            spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
        )
        response = RunResponse(
            run_id="run-123",
            accepted=True,
            status=RunStatus(run_id="run-123", state="queued"),
            links={"self": "/runs/run-123"},
        )
        lease = RunLease(
            run_id="run-123",
            request=request,
            response=response,
            claimed_by="worker-a",
            claimed_at="2026-04-13T15:51:00Z",
            metadata={"worker_id": "worker-a"},
        )
        result = RunResult(
            run_id="run-123",
            ok=True,
            verdict="PASSED",
            state="completed",
            run_dir="/tmp/testfabric/runs/run-123",
            summary_path="/tmp/testfabric/runs/run-123/summary.json",
            status=RunStatus(run_id="run-123", state="completed", verdict="PASSED"),
            summary={"stages_total": 2, "jobs_total": 8},
            metadata={"team": "platform"},
        )
        query = RunQuery(
            team="platform",
            project="api-gateway",
            branch="main",
            status=["completed", "failed"],
            tags=["nightly"],
            limit=25,
            offset=10,
            sort="-started_at",
        )
        compare = CompareRequest(
            left_run_id="run-123",
            right_run_id="run-122",
            fields=["duration_seconds", "watch_hits", "failure_headline"],
            team="platform",
            project="api-gateway",
        )
        replay = ReplayRequest(
            run_id="run-123",
            mode="failed-only",
            stage_id="stage-1",
            local=True,
            overrides={"max_retries": 1},
        )

        self.assertEqual(RunResult.model_validate(result.model_dump(exclude_none=True)), result)
        self.assertEqual(RunLease.model_validate(lease.model_dump(exclude_none=True)), lease)
        self.assertEqual(RunQuery.model_validate(query.model_dump(exclude_none=True)), query)
        self.assertEqual(CompareRequest.model_validate(compare.model_dump(exclude_none=True)), compare)
        self.assertEqual(ReplayRequest.model_validate(replay.model_dump(exclude_none=True)), replay)

    def test_run_request_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            RunRequest.model_validate({"run_id": "run-123", "unknown_field": "boom"})

    def test_worker_registration_and_status_round_trip(self) -> None:
        registration = WorkerRegistration(
            worker_id="worker-a",
            mode="remote",
            host="1.1.1.1",
            user="root",
            labels=["linux", "docker"],
            capacity=4,
            health_state="healthy",
            health_reasons=["disk_ok"],
            metadata={"region": "us-east"},
        )
        status = WorkerStatus(
            worker_id="worker-a",
            mode="remote",
            host="1.1.1.1",
            user="root",
            labels=["linux", "docker"],
            capacity=4,
            active_leases=2,
            health_state="healthy",
            health_reasons=["disk_ok"],
            last_heartbeat_at="2026-04-13T15:51:00Z",
            metadata={"region": "us-east"},
        )

        self.assertEqual(WorkerRegistration.model_validate(registration.model_dump(exclude_none=True)), registration)
        self.assertEqual(WorkerStatus.model_validate(status.model_dump(exclude_none=True)), status)


if __name__ == "__main__":
    unittest.main()
