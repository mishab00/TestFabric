from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock

from testfabric.api.store import SqliteRunStore
from testfabric.core.contracts import RunRequest, RunResult
from testfabric.spec.schema import RunSpec
from testfabric.worker import WorkerRuntime


class WorkerRuntimeTests(unittest.TestCase):
    def _make_spec(self, root: Path) -> RunSpec:
        spec_path = root / "run.yaml"
        spec_path.write_text(
            textwrap.dedent(
                """
                pipeline:
                  stages:
                    - suite: smoke

                suites:
                  smoke:
                    steps:
                      - name: echo
                        shell: echo ok
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return RunSpec.load(str(spec_path))

    def test_run_once_claims_executes_and_completes_queued_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = SqliteRunStore(database_url=f"sqlite:///{root / 'worker.sqlite3'}")
            spec = self._make_spec(root)
            request = RunRequest(
                run_id="run-001",
                mode="run",
                spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                spec=spec.model_dump(mode="json", exclude_none=True),
                team="platform",
                project="api-gateway",
            )
            store.create_run(request)

            executor = Mock(
                return_value=RunResult(
                    run_id="run-001",
                    ok=True,
                    verdict="PASSED",
                    state="completed",
                    summary={"ok": True},
                    metadata={"worker_id": "worker-a"},
                )
            )

            runtime = WorkerRuntime(store=store, executor=executor)
            result = runtime.run_once(worker_id="worker-a")

            self.assertIsNotNone(result)
            self.assertTrue(result.ok)
            self.assertEqual(result.run_id, "run-001")
            self.assertEqual(executor.call_count, 1)
            self.assertEqual(executor.call_args.args[0].run_id, "run-001")
            self.assertEqual(executor.call_args.args[0].claimed_by, "worker-a")

            persisted = store.get_run("run-001")
            assert persisted is not None
            self.assertEqual(persisted.status.state, "completed")
            self.assertEqual(persisted.status.metadata["worker_id"], "worker-a")
            self.assertIsNotNone(persisted.status.heartbeat_at)
            self.assertIn("last_heartbeat_at", persisted.status.metadata)

    def test_run_once_returns_none_when_queue_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SqliteRunStore(database_url=f"sqlite:///{Path(tmpdir) / 'worker.sqlite3'}")
            runtime = WorkerRuntime(store=store)

            self.assertIsNone(runtime.run_once(worker_id="worker-a"))

    def test_serve_processes_runs_until_queue_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            store = SqliteRunStore(database_url=f"sqlite:///{root / 'worker.sqlite3'}")
            spec = self._make_spec(root)
            store.create_run(
                RunRequest(
                    run_id="run-001",
                    mode="run",
                    spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                    spec=spec.model_dump(mode="json", exclude_none=True),
                    team="platform",
                    project="api-gateway",
                )
            )
            store.create_run(
                RunRequest(
                    run_id="run-002",
                    mode="run",
                    spec_yaml="pipeline:\n  stages:\n    - suite: smoke\n",
                    spec=spec.model_dump(mode="json", exclude_none=True),
                    team="platform",
                    project="api-gateway",
                )
            )

            executor = Mock(
                side_effect=[
                    RunResult(
                        run_id="run-001",
                        ok=True,
                        verdict="PASSED",
                        state="completed",
                        summary={"ok": True},
                        metadata={"worker_id": "worker-a"},
                    ),
                    RunResult(
                        run_id="run-002",
                        ok=False,
                        verdict="FAILED",
                        state="failed",
                        error="boom",
                        summary={"ok": False},
                        metadata={"worker_id": "worker-a"},
                    ),
                ]
            )

            runtime = WorkerRuntime(store=store, executor=executor)
            summary = runtime.serve(worker_id="worker-a", max_idle_polls=1, idle_sleep_seconds=0.0)

            self.assertEqual(summary.runs_claimed, 2)
            self.assertEqual(summary.runs_completed, 1)
            self.assertEqual(summary.runs_failed, 1)
            self.assertEqual(summary.idle_polls, 1)
            self.assertEqual(summary.stopped_reason, "idle")
            self.assertEqual(executor.call_count, 2)
            self.assertEqual(store.get_run("run-001").status.state, "completed")
            self.assertEqual(store.get_run("run-002").status.state, "failed")


if __name__ == "__main__":
    unittest.main()
