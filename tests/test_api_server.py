from __future__ import annotations

import json
import tempfile
import unittest
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from testfabric.api import create_app
from testfabric.api.store import WorkerRecord
from testfabric.core.contracts import ReplayRequest, RunRequest, RunResult, WorkerRegistration


class ApiServerTests(unittest.TestCase):
    def _database_url(self, tmpdir: str) -> str:
        return f"sqlite:///{Path(tmpdir) / 'testfabric.sqlite3'}"

    def test_post_run_persists_through_a_real_sqlite_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)

            request = RunRequest(
                run_id="run-123",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                repo_url="git@example.com:org/repo.git",
                ref="main",
                team="platform",
                project="api-gateway",
                workspace="/workspaces/api-gateway",
                context="team-staging",
                inputs={"env": "staging"},
                tags=["nightly"],
                metadata={"triggered_by": "alice"},
            )

            app = create_app(database_url=database_url)
            with TestClient(app) as client:
                response = client.post("/runs", json=request.model_dump(exclude_none=True))

                self.assertEqual(response.status_code, 202)
                created = response.json()
                self.assertEqual(created["run_id"], "run-123")
                self.assertTrue(created["accepted"])
                self.assertEqual(created["status"]["state"], "queued")
                self.assertEqual(created["status"]["metadata"]["team"], "platform")
                self.assertEqual(created["links"]["self"], "/runs/run-123")

                fetched = client.get("/runs/run-123")
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json(), created)

                listing = client.get("/runs")
                self.assertEqual(listing.status_code, 200)
                payload = listing.json()
                self.assertEqual(payload["total"], 1)
                self.assertEqual(len(payload["items"]), 1)
                self.assertEqual(payload["items"][0]["run_id"], "run-123")
                self.assertEqual(payload["items"][0]["status"]["state"], "queued")

            reopened = create_app(database_url=database_url)
            with TestClient(reopened) as client:
                fetched = client.get("/runs/run-123")
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["run_id"], "run-123")

                listing = client.get("/runs")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["total"], 1)

    def test_post_run_generates_run_id_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            request = RunRequest(
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                team="platform",
            )

            with TestClient(app) as client:
                response = client.post("/runs", json=request.model_dump(exclude_none=True))

                self.assertEqual(response.status_code, 202)
                created = response.json()
                self.assertTrue(str(created["run_id"]).startswith("run-"))
                self.assertEqual(created["status"]["state"], "queued")

                fetched = client.get(f"/runs/{created['run_id']}")
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["run_id"], created["run_id"])

    def test_queue_claims_runs_in_fifo_order_and_marks_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            first = RunRequest(run_id="run-001", mode="run", team="platform")
            second = RunRequest(run_id="run-002", mode="run", team="platform")

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=first.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=second.model_dump(exclude_none=True)).status_code, 202)

                queue = client.get("/queue")
                self.assertEqual(queue.status_code, 200)
                self.assertEqual(queue.json()["total"], 2)
                self.assertEqual([item["run_id"] for item in queue.json()["items"]], ["run-001", "run-002"])

                claimed = client.post("/queue/claim", json={"worker_id": "worker-a"})
                self.assertEqual(claimed.status_code, 200)
                first_claim = claimed.json()
                self.assertEqual(first_claim["run_id"], "run-001")
                self.assertEqual(first_claim["request"]["run_id"], "run-001")
                self.assertEqual(first_claim["response"]["status"]["state"], "running")
                self.assertEqual(first_claim["response"]["status"]["metadata"]["worker_id"], "worker-a")

                queue_after_first = client.get("/queue")
                self.assertEqual(queue_after_first.status_code, 200)
                self.assertEqual(queue_after_first.json()["total"], 1)
                self.assertEqual(queue_after_first.json()["items"][0]["run_id"], "run-002")

                claimed_second = client.post("/queue/claim", json={"worker_id": "worker-b"})
                self.assertEqual(claimed_second.status_code, 200)
                second_claim = claimed_second.json()
                self.assertEqual(second_claim["run_id"], "run-002")
                self.assertEqual(second_claim["response"]["status"]["state"], "running")
                self.assertEqual(second_claim["response"]["status"]["metadata"]["worker_id"], "worker-b")

                completion = client.post(
                    "/runs/run-001/complete",
                    json=RunResult(
                        run_id="run-001",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        metadata={"worker_id": "worker-a"},
                    ).model_dump(exclude_none=True),
                )
                self.assertEqual(completion.status_code, 200)
                self.assertEqual(completion.json()["status"]["state"], "completed")

                queue_empty = client.get("/queue")
                self.assertEqual(queue_empty.status_code, 200)
                self.assertEqual(queue_empty.json()["total"], 0)

                none_left = client.post("/queue/claim", json={"worker_id": "worker-c"})
                self.assertEqual(none_left.status_code, 204)

    def test_queue_claim_honors_priority_and_worker_label_affinity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            low = RunRequest(
                run_id="run-low",
                mode="run",
                team="platform",
                project="api-gateway",
                priority=1,
                routing_labels=["linux"],
            )
            high = RunRequest(
                run_id="run-high",
                mode="run",
                team="platform",
                project="api-gateway",
                priority=10,
                routing_labels=["linux", "docker"],
            )
            gpu = RunRequest(
                run_id="run-gpu",
                mode="run",
                team="platform",
                project="api-gateway",
                priority=99,
                routing_labels=["gpu"],
            )

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=low.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=high.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=gpu.model_dump(exclude_none=True)).status_code, 202)

                claimed = client.post(
                    "/queue/claim",
                    json={
                        "worker_id": "worker-a",
                        "worker_labels": ["linux", "docker"],
                        "policy": "priority",
                    },
                )
                self.assertEqual(claimed.status_code, 200)
                first_claim = claimed.json()
                self.assertEqual(first_claim["run_id"], "run-high")
                self.assertEqual(first_claim["response"]["status"]["metadata"]["scheduler_policy"], "priority")
                self.assertEqual(first_claim["response"]["status"]["metadata"]["worker_labels"], ["linux", "docker"])

                next_claim = client.post(
                    "/queue/claim",
                    json={
                        "worker_id": "worker-b",
                        "worker_labels": ["linux"],
                        "policy": "fifo",
                    },
                )
                self.assertEqual(next_claim.status_code, 200)
                self.assertEqual(next_claim.json()["run_id"], "run-low")

                no_match = client.post(
                    "/queue/claim",
                    json={
                        "worker_id": "worker-c",
                        "worker_labels": ["linux"],
                        "policy": "fifo",
                    },
                )
                self.assertEqual(no_match.status_code, 204)

    def test_workers_can_be_registered_heartbeated_and_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            worker = WorkerRegistration(
                worker_id="worker-a",
                mode="remote",
                host="1.1.1.1",
                user="root",
                labels=["linux", "docker"],
                capacity=2,
                health_state="healthy",
                health_reasons=["disk_ok"],
                metadata={"region": "us-east"},
            )

            with TestClient(app) as client:
                created = client.post("/workers", json=worker.model_dump(exclude_none=True))
                self.assertEqual(created.status_code, 201)
                self.assertEqual(created.json()["worker_id"], "worker-a")
                self.assertEqual(created.json()["capacity"], 2)
                self.assertEqual(created.json()["labels"], ["linux", "docker"])

                heartbeat = client.post(
                    "/workers/worker-a/heartbeat",
                    json={
                        "metadata": {"seen_by": "worker-a"},
                        "health_state": "degraded",
                        "health_reasons": ["cpu_high"],
                    },
                )
                self.assertEqual(heartbeat.status_code, 200)
                heartbeat_payload = heartbeat.json()
                self.assertEqual(heartbeat_payload["health_state"], "degraded")
                self.assertEqual(heartbeat_payload["health_reasons"], ["cpu_high"])
                self.assertIsNotNone(heartbeat_payload["last_heartbeat_at"])
                self.assertEqual(heartbeat_payload["metadata"]["seen_by"], "worker-a")

                listing = client.get("/workers")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()[0]["worker_id"], "worker-a")

                fetched = client.get("/workers/worker-a")
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["worker_id"], "worker-a")

    def test_workers_become_stale_when_heartbeat_is_old_and_cannot_claim_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir), worker_heartbeat_timeout_seconds=1.0)
            worker = WorkerRegistration(worker_id="worker-a", capacity=1)
            run = RunRequest(run_id="run-stale", mode="run", team="platform")

            with TestClient(app) as client:
                self.assertEqual(client.post("/workers", json=worker.model_dump(exclude_none=True)).status_code, 201)
                self.assertEqual(client.post("/runs", json=run.model_dump(exclude_none=True)).status_code, 202)

                store = client.app.state.store
                assert store is not None
                old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=10)
                with store._session() as session:  # noqa: SLF001 - test needs direct fixture control
                    record = session.get(WorkerRecord, "worker-a")
                    self.assertIsNotNone(record)
                    assert record is not None
                    record.last_heartbeat_at = old_heartbeat
                    record.updated_at = old_heartbeat
                    session.add(record)
                    session.commit()

                fetched = client.get("/workers/worker-a")
                self.assertEqual(fetched.status_code, 200)
                fetched_payload = fetched.json()
                self.assertEqual(fetched_payload["health_state"], "stale")
                self.assertIn("heartbeat_stale", fetched_payload["health_reasons"])
                self.assertGreaterEqual(fetched_payload["metadata"]["heartbeat_age_seconds"], 1)

                listed = client.get("/workers")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()[0]["health_state"], "stale")

                claim = client.post("/queue/claim", json={"worker_id": "worker-a"})
                self.assertEqual(claim.status_code, 409)
                self.assertIn("Worker unavailable", claim.json()["detail"])

    def test_worker_capacity_releases_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            first = RunRequest(run_id="run-001", mode="run", team="platform", project="api-gateway")
            second = RunRequest(run_id="run-002", mode="run", team="platform", project="api-gateway")

            with TestClient(app) as client:
                self.assertEqual(client.post("/workers", json=WorkerRegistration(worker_id="worker-a", capacity=1).model_dump(exclude_none=True)).status_code, 201)
                self.assertEqual(client.post("/runs", json=first.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=second.model_dump(exclude_none=True)).status_code, 202)

                first_claim = client.post(
                    "/queue/claim",
                    json={
                        "worker_id": "worker-a",
                        "worker_labels": ["linux"],
                        "policy": "fifo",
                    },
                )
                self.assertEqual(first_claim.status_code, 200)
                self.assertEqual(first_claim.json()["run_id"], "run-001")

                blocked = client.post(
                    "/queue/claim",
                    json={
                        "worker_id": "worker-a",
                        "worker_labels": ["linux"],
                        "policy": "fifo",
                    },
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertIn("Worker unavailable", blocked.json()["detail"])

                completion = client.post(
                    "/runs/run-001/complete",
                    json=RunResult(
                        run_id="run-001",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        metadata={"worker_id": "worker-a"},
                    ).model_dump(exclude_none=True),
                )
                self.assertEqual(completion.status_code, 200)

                next_claim = client.post(
                    "/queue/claim",
                    json={
                        "worker_id": "worker-a",
                        "worker_labels": ["linux"],
                        "policy": "fifo",
                    },
                )
                self.assertEqual(next_claim.status_code, 200)
                self.assertEqual(next_claim.json()["run_id"], "run-002")

    def test_heartbeat_and_cancel_update_active_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(database_url=self._database_url(tmpdir))
            request = RunRequest(run_id="run-control", mode="run", team="platform", project="api-gateway")

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=request.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/queue/claim", json={"worker_id": "worker-a"}).status_code, 200)

                heartbeat = client.post(
                    "/runs/run-control/heartbeat",
                    json={"worker_id": "worker-a"},
                )
                self.assertEqual(heartbeat.status_code, 200)
                heartbeat_payload = heartbeat.json()
                self.assertEqual(heartbeat_payload["status"]["state"], "running")
                self.assertEqual(heartbeat_payload["status"]["metadata"]["worker_id"], "worker-a")
                self.assertEqual(heartbeat_payload["status"]["progress"]["heartbeat"], True)
                self.assertIsNotNone(heartbeat_payload["status"]["heartbeat_at"])

                cancel = client.post(
                    "/runs/run-control/cancel",
                    json={"worker_id": "worker-a", "reason": "manual stop"},
                )
                self.assertEqual(cancel.status_code, 200)
                cancel_payload = cancel.json()
                self.assertEqual(cancel_payload["status"]["state"], "canceled")
                self.assertEqual(cancel_payload["status"]["metadata"]["canceled_reason"], "manual stop")
                self.assertEqual(cancel_payload["status"]["progress"]["canceled"], True)

                queue = client.get("/queue")
                self.assertEqual(queue.status_code, 200)
                self.assertEqual(queue.json()["total"], 0)

    def test_get_missing_run_returns_404(self) -> None:
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/runs/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Run not found: does-not-exist")

    def test_api_requires_bearer_token_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url, api_token="XXXX")
            request = RunRequest(run_id="secure-run", mode="run", team="platform")

            with TestClient(app) as client:
                denied = client.post("/runs", json=request.model_dump(exclude_none=True))
                self.assertEqual(denied.status_code, 401)
                self.assertEqual(denied.json()["detail"], "Unauthorized")

                allowed = client.post(
                    "/runs",
                    json=request.model_dump(exclude_none=True),
                    headers={"Authorization": "Bearer XXXX"},
                )
                self.assertEqual(allowed.status_code, 202)
                self.assertEqual(allowed.json()["run_id"], "secure-run")

                health = client.get("/health")
                self.assertEqual(health.status_code, 200)

    def test_json_contracts_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            with TestClient(app) as client:
                response = client.post("/runs", json=RunRequest(run_id="json-run", mode="run").model_dump(exclude_none=True))
        self.assertEqual(response.status_code, 202)
        json.dumps(response.json(), sort_keys=True)

    def test_get_run_detail_exposes_request_status_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            request = RunRequest(
                run_id="run-detail",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                team="platform",
                project="api-gateway",
                workspace="/workspaces/api-gateway",
                context="team-staging",
                tags=["nightly"],
                metadata={"triggered_by": "alice"},
            )

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=request.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(
                    client.post(
                        "/queue/claim",
                        json={"worker_id": "worker-a"},
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.post(
                        "/runs/run-detail/complete",
                        json=RunResult(
                            run_id="run-detail",
                            ok=True,
                            verdict="PASSED",
                            state="completed",
                            run_dir="/tmp/testfabric/run-detail",
                            summary_path="/tmp/testfabric/run-detail/summary.json",
                            summary={"run": {"run_id": "run-detail", "verdict": "PASSED"}},
                            metadata={"worker_id": "worker-a"},
                        ).model_dump(exclude_none=True),
                    ).status_code,
                    200,
                )
                response = client.get("/runs/run-detail/detail")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["run_id"], "run-detail")
            self.assertEqual(payload["request"]["team"], "platform")
            self.assertEqual(payload["status"]["state"], "completed")
            self.assertEqual(payload["result"]["run_dir"], "/tmp/testfabric/run-detail")
            self.assertEqual(payload["summary"]["run"]["verdict"], "PASSED")

    def test_get_run_events_reads_jsonl_from_the_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            run_request = RunRequest(
                run_id="run-events",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                spec={
                    "run": {
                        "artifacts_dir": str(Path(tmpdir) / "artifacts"),
                        "runs_subdir": "runs",
                    }
                },
                team="platform",
                project="api-gateway",
                metadata={
                    "run_dir": str(Path(tmpdir) / "artifacts" / "runs" / "run-events"),
                },
            )

            with TestClient(app) as client:
                created = client.post("/runs", json=run_request.model_dump(exclude_none=True))
                self.assertEqual(created.status_code, 202)
                created_payload = created.json()
                run_dir = Path(created_payload["status"]["metadata"]["run_dir"])
                events_path = run_dir / "events.jsonl"
                events_path.parent.mkdir(parents=True, exist_ok=True)
                events_path.write_text(
                    "\n".join(
                        [
                            json.dumps(
                                {
                                    "seq": 1,
                                    "ts": "2026-04-13T10:00:00Z",
                                    "component": "run",
                                    "action": "start",
                                    "status": "start",
                                    "message": "run:start start",
                                }
                            ),
                            json.dumps(
                                {
                                    "seq": 2,
                                    "ts": "2026-04-13T10:00:01Z",
                                    "component": "job",
                                    "action": "start",
                                    "status": "start",
                                    "message": "job:start start",
                                }
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                response = client.get("/runs/run-events/events", params={"offset": 1, "limit": 1})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["run_id"], "run-events")
            self.assertEqual(payload["total"], 2)
            self.assertEqual(payload["next_offset"], 2)
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(payload["items"][0]["seq"], 2)
            self.assertEqual(payload["items"][0]["component"], "job")

    def test_get_run_events_follow_waits_for_new_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            run_request = RunRequest(
                run_id="run-follow",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                spec={
                    "run": {
                        "artifacts_dir": str(Path(tmpdir) / "artifacts"),
                        "runs_subdir": "runs",
                    }
                },
                team="platform",
                project="api-gateway",
                metadata={
                    "run_dir": str(Path(tmpdir) / "artifacts" / "runs" / "run-follow"),
                },
            )

            with TestClient(app) as client:
                created = client.post("/runs", json=run_request.model_dump(exclude_none=True))
                self.assertEqual(created.status_code, 202)
                created_payload = created.json()
                run_dir = Path(created_payload["status"]["metadata"]["run_dir"])
                events_path = run_dir / "events.jsonl"
                events_path.parent.mkdir(parents=True, exist_ok=True)
                events_path.write_text(
                    json.dumps(
                        {
                            "seq": 1,
                            "ts": "2026-04-13T10:00:00Z",
                            "component": "run",
                            "action": "start",
                            "status": "start",
                            "message": "run:start start",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                def append_event() -> None:
                    time.sleep(0.2)
                    events_path.parent.mkdir(parents=True, exist_ok=True)
                    with events_path.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "seq": 2,
                                    "ts": "2026-04-13T10:00:01Z",
                                    "component": "job",
                                    "action": "start",
                                    "status": "start",
                                    "message": "job:start start",
                                }
                            )
                            + "\n"
                        )

                thread = threading.Thread(target=append_event, daemon=True)
                thread.start()
                response = client.get("/runs/run-follow/events", params={"offset": 1, "limit": 1, "follow": True})
                thread.join(timeout=2.0)

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["run_id"], "run-follow")
            self.assertEqual(payload["total"], 2)
            self.assertTrue(payload.get("follow"))
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(payload["items"][0]["seq"], 2)
            self.assertEqual(payload["items"][0]["component"], "job")

    def test_get_run_artifacts_lists_directories_and_file_previews(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            run_dir = Path(tmpdir) / "artifacts" / "runs" / "run-artifacts"
            run_request = RunRequest(
                run_id="run-artifacts",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                metadata={"run_dir": str(run_dir)},
            )

            with TestClient(app) as client:
                created = client.post("/runs", json=run_request.model_dump(exclude_none=True))
                self.assertEqual(created.status_code, 202)
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "logs").mkdir(parents=True, exist_ok=True)
                (run_dir / "logs" / "build.log").write_text("hello\nworld\n", encoding="utf-8")
                (run_dir / "report.txt").write_text("summary line\n", encoding="utf-8")

                tree = client.get("/runs/run-artifacts/artifacts")
                self.assertEqual(tree.status_code, 200)
                tree_payload = tree.json()
                self.assertEqual(tree_payload["run_id"], "run-artifacts")
                self.assertEqual(tree_payload["kind"], "tree")
                self.assertEqual(tree_payload["total"], 3)
                self.assertEqual(
                    sorted(item["path"] for item in tree_payload["items"]),
                    ["logs", "logs/build.log", "report.txt"],
                )

                file_view = client.get("/runs/run-artifacts/artifacts", params={"path": "logs/build.log"})
                self.assertEqual(file_view.status_code, 200)
                file_payload = file_view.json()
                self.assertEqual(file_payload["kind"], "file")
                self.assertEqual(file_payload["path"], "logs/build.log")
                self.assertTrue(file_payload["is_text"])
                self.assertIn("hello", file_payload["content"])

    def test_get_run_artifacts_search_matches_text_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            run_dir = Path(tmpdir) / "artifacts" / "runs" / "run-search"
            run_request = RunRequest(
                run_id="run-search",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                metadata={"run_dir": str(run_dir)},
            )

            with TestClient(app) as client:
                created = client.post("/runs", json=run_request.model_dump(exclude_none=True))
                self.assertEqual(created.status_code, 202)
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "logs").mkdir(parents=True, exist_ok=True)
                (run_dir / "logs" / "build.log").write_text("hello\nneedle line\nbye\n", encoding="utf-8")
                (run_dir / "report.txt").write_text("needle in a report\n", encoding="utf-8")

                search = client.get("/runs/run-search/artifacts", params={"search": "needle"})

            self.assertEqual(search.status_code, 200)
            payload = search.json()
            self.assertEqual(payload["kind"], "search")
            self.assertEqual(payload["query"], "needle")
            self.assertEqual(payload["total"], 2)
            self.assertTrue(all("line_number" in item for item in payload["items"]))
            self.assertTrue(any(item["path"].endswith("build.log") for item in payload["items"]))

    def test_runs_can_be_filtered_by_team_project_status_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            matching = RunRequest(
                run_id="run-match",
                mode="run",
                team="platform",
                project="api-gateway",
                tags=["nightly", "docker"],
            )
            other = RunRequest(
                run_id="run-other",
                mode="run",
                team="infra",
                project="api-gateway",
                tags=["nightly"],
            )

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=matching.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=other.model_dump(exclude_none=True)).status_code, 202)

                client.post(
                    "/runs/run-match/complete",
                    json=RunResult(
                        run_id="run-match",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        metadata={"team": "platform", "project": "api-gateway"},
                    ).model_dump(exclude_none=True),
                )

                response = client.get(
                    "/runs",
                    params={
                        "team": "platform",
                        "project": "api-gateway",
                        "status": "completed",
                        "tags": "nightly",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["total"], 1)
            self.assertEqual([item["run_id"] for item in payload["items"]], ["run-match"])

    def test_dashboard_summarizes_runs_and_recent_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            first = RunRequest(run_id="run-001", mode="run", team="platform", project="api-gateway", tags=["nightly"])
            second = RunRequest(run_id="run-002", mode="run", team="platform", project="api-gateway", tags=["nightly"])
            third = RunRequest(run_id="run-003", mode="run", team="infra", project="deploy", tags=["manual"])

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=first.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=second.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=third.model_dump(exclude_none=True)).status_code, 202)

                self.assertEqual(
                    client.post("/queue/claim", json={"worker_id": "worker-a"}).status_code,
                    200,
                )
                self.assertEqual(
                    client.post(
                        "/runs/run-001/complete",
                        json=RunResult(
                            run_id="run-001",
                            ok=True,
                            verdict="PASSED",
                            state="completed",
                            metadata={"team": "platform", "project": "api-gateway"},
                        ).model_dump(exclude_none=True),
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.post("/queue/claim", json={"worker_id": "worker-b"}).status_code,
                    200,
                )

                response = client.get("/dashboard", params={"limit": 2})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["total"], 3)
            self.assertEqual(payload["counts_by_state"]["completed"], 1)
            self.assertEqual(payload["counts_by_state"]["running"], 1)
            self.assertEqual(payload["counts_by_state"]["queued"], 1)
            self.assertEqual(payload["counts_by_team"]["platform"], 2)
            self.assertEqual(payload["counts_by_project"]["api-gateway"], 2)
            self.assertEqual(len(payload["recent_runs"]), 2)
            self.assertEqual(payload["recent_runs"][0]["run_id"], "run-003")

    def test_compare_and_replay_runs_are_exposed_by_the_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_url = self._database_url(tmpdir)
            app = create_app(database_url=database_url)
            first = RunRequest(
                run_id="run-001",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                team="platform",
                project="api-gateway",
            )
            second = RunRequest(
                run_id="run-002",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                team="platform",
                project="api-gateway",
            )

            with TestClient(app) as client:
                self.assertEqual(client.post("/runs", json=first.model_dump(exclude_none=True)).status_code, 202)
                self.assertEqual(client.post("/runs", json=second.model_dump(exclude_none=True)).status_code, 202)
                client.post(
                    "/runs/run-001/complete",
                    json=RunResult(
                        run_id="run-001",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        metadata={"team": "platform"},
                    ).model_dump(exclude_none=True),
                )

                compare = client.get("/compare/run-001/run-002")
                self.assertEqual(compare.status_code, 200)
                payload = compare.json()
                self.assertEqual(payload["left_run_id"], "run-001")
                self.assertEqual(payload["right_run_id"], "run-002")
                self.assertIn("duration_delta_seconds", payload["summary"])

                replay = client.post(
                    "/runs/run-001/replay",
                    json=ReplayRequest(run_id="run-001", mode="exact").model_dump(exclude_none=True),
                )
                self.assertEqual(replay.status_code, 202)
                replayed = replay.json()
                self.assertNotEqual(replayed["run_id"], "run-001")
                self.assertEqual(replayed["status"]["state"], "queued")
                self.assertEqual(replayed["status"]["metadata"]["replayed_from"], "run-001")


if __name__ == "__main__":
    unittest.main()
