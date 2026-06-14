from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from testfabric.api import create_app
from testfabric.core.contracts import RunRequest, RunResult, WorkerRegistration
from testfabric.worker import RemoteWorkerRuntime, WorkerClient


class WorkerClientTests(unittest.TestCase):
    def _database_url(self, tmpdir: str) -> str:
        return f"sqlite:///{Path(tmpdir) / 'testfabric.sqlite3'}"

    def _requester(self, client: TestClient):
        def _send(method: str, url: str, body, headers, timeout):
            parsed = urlparse(url)
            response = client.request(
                method,
                parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                headers=headers,
                json=body,
            )
            return response.status_code, response.text

        return _send

    def test_worker_client_can_register_claim_and_complete_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            with TestClient(app) as backend:
                client = WorkerClient(api_url="http://testserver", requester=self._requester(backend))

                created = backend.post(
                    "/runs",
                    json=RunRequest(
                        run_id="run-001",
                        mode="run",
                        team="platform",
                        project="api-gateway",
                        routing_labels=["linux"],
                    ).model_dump(exclude_none=True),
                )
                self.assertEqual(created.status_code, 202)

                worker = client.register_worker(
                    WorkerRegistration(
                        worker_id="worker-a",
                        mode="remote",
                        host="1.1.1.1",
                        user="root",
                        labels=["linux", "docker"],
                        capacity=1,
                    )
                )
                self.assertEqual(worker.worker_id, "worker-a")
                self.assertEqual(worker.capacity, 1)

                claimed = client.claim_next_run(worker_id="worker-a", worker_labels=["linux", "docker"], policy="fifo")
                assert claimed is not None
                self.assertEqual(claimed.run_id, "run-001")
                self.assertEqual(claimed.claimed_by, "worker-a")

                worker_after_claim = client.get_worker("worker-a")
                self.assertEqual(worker_after_claim.active_leases, 1)

                heartbeat = client.heartbeat_run("run-001", worker_id="worker-a")
                self.assertEqual(heartbeat.status.run_id, "run-001")
                self.assertEqual(heartbeat.status.metadata["worker_id"], "worker-a")

                completion = client.complete_run(
                    "run-001",
                    RunResult(
                        run_id="run-001",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        metadata={"worker_id": "worker-a"},
                    ),
                )
                self.assertEqual(completion.status.run_id, "run-001")
                self.assertEqual(completion.status.state, "completed")

                worker_after_complete = client.get_worker("worker-a")
                self.assertEqual(worker_after_complete.active_leases, 0)

                run_detail = backend.get("/runs/run-001")
                self.assertEqual(run_detail.status_code, 200)
                self.assertEqual(run_detail.json()["status"]["state"], "completed")

    def test_remote_worker_runtime_claims_and_completes_one_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            with TestClient(app) as backend:
                client = WorkerClient(api_url="http://testserver", requester=self._requester(backend))

                backend.post(
                    "/runs",
                    json=RunRequest(
                        run_id="run-remote",
                        mode="run",
                        team="platform",
                        project="api-gateway",
                        routing_labels=["linux", "docker"],
                    ).model_dump(exclude_none=True),
                )

                executor = Mock(
                    return_value=RunResult(
                        run_id="run-remote",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        summary={"ok": True},
                        metadata={"worker_id": "worker-remote"},
                    )
                )
                runtime = RemoteWorkerRuntime(
                    client=client,
                    worker=WorkerRegistration(
                        worker_id="worker-remote",
                        mode="remote",
                        host="1.1.1.1",
                        user="root",
                        labels=["linux", "docker"],
                        capacity=1,
                    ),
                    executor=executor,
                    heartbeat_interval_seconds=0.0,
                )

                result = runtime.run_once()

                self.assertIsNotNone(result)
                self.assertTrue(result.ok)
                self.assertEqual(result.run_id, "run-remote")
                executor.assert_called_once()
                self.assertEqual(executor.call_args.args[0].run_id, "run-remote")
                self.assertEqual(executor.call_args.args[0].claimed_by, "worker-remote")

                run_detail = backend.get("/runs/run-remote")
                self.assertEqual(run_detail.status_code, 200)
                self.assertEqual(run_detail.json()["status"]["state"], "completed")
                worker = backend.get("/workers/worker-remote")
                self.assertEqual(worker.status_code, 200)
                self.assertEqual(worker.json()["active_leases"], 0)

    def test_remote_worker_runtime_serves_multiple_runs_until_queue_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            with TestClient(app) as backend:
                client = WorkerClient(api_url="http://testserver", requester=self._requester(backend))

                backend.post(
                    "/runs",
                    json=RunRequest(
                        run_id="run-001",
                        mode="run",
                        team="platform",
                        project="api-gateway",
                        routing_labels=["linux", "docker"],
                    ).model_dump(exclude_none=True),
                )
                backend.post(
                    "/runs",
                    json=RunRequest(
                        run_id="run-002",
                        mode="run",
                        team="platform",
                        project="api-gateway",
                        routing_labels=["linux", "docker"],
                    ).model_dump(exclude_none=True),
                )

                executor = Mock(
                    side_effect=[
                        RunResult(
                            run_id="run-001",
                            ok=True,
                            verdict="PASSED",
                            state="completed",
                            summary={"ok": True},
                            metadata={"worker_id": "worker-remote"},
                        ),
                        RunResult(
                            run_id="run-002",
                            ok=False,
                            verdict="FAILED",
                            state="failed",
                            error="boom",
                            summary={"ok": False},
                            metadata={"worker_id": "worker-remote"},
                        ),
                    ]
                )
                runtime = RemoteWorkerRuntime(
                    client=client,
                    worker=WorkerRegistration(
                        worker_id="worker-remote",
                        mode="remote",
                        host="1.1.1.1",
                        user="root",
                        labels=["linux", "docker"],
                        capacity=1,
                    ),
                    executor=executor,
                    heartbeat_interval_seconds=0.0,
                )

                summary = runtime.serve(worker_id="worker-remote", max_idle_polls=1, idle_sleep_seconds=0.0)

                self.assertEqual(summary.runs_claimed, 2)
                self.assertEqual(summary.runs_completed, 1)
                self.assertEqual(summary.runs_failed, 1)
                self.assertEqual(summary.idle_polls, 1)
                self.assertEqual(summary.stopped_reason, "idle")
                self.assertEqual(executor.call_count, 2)

                run_one = backend.get("/runs/run-001")
                run_two = backend.get("/runs/run-002")
                self.assertEqual(run_one.json()["status"]["state"], "completed")
                self.assertEqual(run_two.json()["status"]["state"], "failed")
                worker = backend.get("/workers/worker-remote")
                self.assertEqual(worker.status_code, 200)
                self.assertEqual(worker.json()["active_leases"], 0)


if __name__ == "__main__":
    unittest.main()
