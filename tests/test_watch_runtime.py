from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import RepoSnapshot, RunContext, StageContext
from testfabric.execution.executors.base import ExecutableCommand, ExecResult
from testfabric.execution.suites.base import JobPlan, TestItem
from testfabric.orchestrator.dispatch.threaded import LocalDispatcher
from testfabric.watch.runtime import WatchRuntime
from unittest.mock import patch


@dataclass
class _RecordingEvents:
    events: list[object]

    def emit(self, event):
        self.events.append(event)

    def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None:
        return


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, ctx, cmd, *, on_stdout=None, on_stderr=None):
        self.calls.append(list(cmd.cmd))
        if on_stdout is not None:
            keep = on_stdout("Traceback: boom\n")
            if keep is False:
                return ExecResult(exit_code=130, stdout="", stderr="", timed_out=True)
        return ExecResult(exit_code=0, stdout="ok\n", stderr="")


class _FakeHTTPResponse:
    def __init__(self, *, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body


class WatchRuntimeTests(unittest.TestCase):
    def _make_ctx(self, td: str, *, watch: dict[str, object] | None = None) -> StageContext:
        root = Path(td)
        repo_dir = root / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        spec = SimpleNamespace(
            run=SimpleNamespace(
                artifacts_dir=str(root / "artifacts"),
                runs_subdir="runs",
                workers_tmp=str(root / "workers_tmp"),
            )
        )
        paths = PathManager(spec, "run-001")
        run_ctx = RunContext(
            run_id="run-001",
            mode="run",
            paths=paths,
            repo=RepoSnapshot(repo_path=str(repo_dir), commit_sha="abc123"),
            events=_RecordingEvents([]),
        )
        stage_ref = StageRef(index=1, title="Watch", suite="smoke")
        ctx = StageContext(
            run=run_ctx,
            suite_name="smoke",
            stage_index=1,
            stage_title="Watch",
            stage_slug=stage_ref.slug,
            stage_ref=stage_ref,
            executor="local",
            runner="command",
            kind="command",
            build=False,
            max_workers=1,
            chunk_size=1,
            max_retries=0,
            worker_id="w001",
            watch=dict(watch or {}),
        )
        paths.ensure_run_dirs()
        paths.ensure_worker_tmp_dirs(ctx.worker_id)
        paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)
        return ctx

    def test_session_watch_can_send_response_once(self) -> None:
        events = _RecordingEvents([])
        runtime = WatchRuntime(
            {
                "sources": [
                    {
                        "name": "session_out",
                        "type": "session",
                        "stream": "stdout",
                    }
                ],
                "actions": [
                    {
                        "name": "reply",
                        "type": "send",
                        "value": "secret\n",
                    }
                ],
                "watchers": [
                    {
                        "name": "password_prompt",
                        "source": "session_out",
                        "when": {
                            "match": "Password:",
                        },
                        "then": {
                            "send": "secret",
                        },
                    }
                ],
            },
            events=events,
            run_scope={"run_id": "run-001", "stage_id": "stage-1", "job_id": "job-1"},
        )

        keep_running = runtime.feed_output("Password:\n", stream="stdout", phase="job", context={"attempt": 0})
        repeat = runtime.feed_output("Password:\n", stream="stdout", phase="job", context={"attempt": 0})

        self.assertTrue(keep_running)
        self.assertTrue(repeat)
        self.assertEqual(runtime.sends, ["secret"])
        self.assertEqual(len([ev for ev in events.events if getattr(ev, "component", None) == "watch"]), 1)

    def test_file_watch_reports_only_once_for_repeated_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "logs" / "app.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("Traceback: boom\n", encoding="utf-8")

            events = _RecordingEvents([])
            runtime = WatchRuntime(
                {
                    "sources": [
                        {
                            "name": "app_log",
                            "type": "file",
                            "path": "logs/app.log",
                            "mode": "snapshot",
                            "phases": ["stage"],
                        }
                    ],
                    "watchers": [
                        {
                            "name": "traceback_once",
                            "source": "app_log",
                            "when": {
                                "match": "Traceback",
                            },
                            "then": {
                                "report": {
                                    "message": "Traceback detected in app.log",
                                },
                            },
                        }
                    ],
                },
                events=events,
                base_dir=root,
                run_scope={"run_id": "run-001", "stage_id": "stage-1"},
            )

            runtime.poll_once(phase="stage")
            runtime.poll_once(phase="stage")

            watch_events = [ev for ev in events.events if getattr(ev, "component", None) == "watch"]
            self.assertEqual(len(watch_events), 1)
            self.assertEqual(runtime.reports[0]["watcher"], "traceback_once")
            self.assertEqual(runtime.reports[0]["message"], "Traceback detected in app.log")

    def test_sequence_watch_reports_completion_in_order(self) -> None:
        events = _RecordingEvents([])
        runtime = WatchRuntime(
            {
                "sources": [
                    {
                        "name": "job_out",
                        "type": "session",
                        "stream": "stdout",
                    }
                ],
                "watchers": [
                    {
                        "name": "job_flow_complete",
                        "source": "job_out",
                        "when": {
                            "sequence": [
                                "job started",
                                "job processed",
                                "job finished",
                            ]
                        },
                        "then": {
                            "report": {
                                "message": "Job flow reached finished",
                            },
                        },
                    }
                ],
            },
            events=events,
            run_scope={"run_id": "run-001", "stage_id": "stage-1", "job_id": "job-1"},
        )

        self.assertTrue(runtime.feed_output("job started\n", stream="stdout", phase="job", context={"attempt": 0}))
        self.assertTrue(runtime.feed_output("job processed\n", stream="stdout", phase="job", context={"attempt": 0}))
        self.assertTrue(runtime.feed_output("job finished\n", stream="stdout", phase="job", context={"attempt": 0}))

        watch_events = [ev for ev in events.events if getattr(ev, "component", None) == "watch"]
        self.assertEqual(len(watch_events), 1)
        self.assertEqual(runtime.reports[0]["watcher"], "job_flow_complete")
        self.assertEqual(runtime.reports[0]["message"], "Job flow reached finished")
        self.assertEqual(runtime.reports[0]["match"], "job started -> job processed -> job finished")

    def test_sequence_watch_missing_final_milestone_fails_on_finalize(self) -> None:
        events = _RecordingEvents([])
        runtime = WatchRuntime(
            {
                "sources": [
                    {
                        "name": "job_out",
                        "type": "session",
                        "stream": "stdout",
                    }
                ],
                "watchers": [
                    {
                        "name": "job_flow_complete",
                        "source": "job_out",
                        "missing": "fail",
                        "when": {
                            "sequence": [
                                "job started",
                                "job processed",
                                "job finished",
                            ]
                        },
                        "then": {
                            "report": {
                                "message": "Job flow reached finished",
                            },
                        },
                    }
                ],
            },
            events=events,
            run_scope={"run_id": "run-001", "stage_id": "stage-1", "job_id": "job-1"},
        )

        self.assertTrue(runtime.feed_output("job started\n", stream="stdout", phase="job", context={"attempt": 0}))
        self.assertTrue(runtime.feed_output("job processed\n", stream="stdout", phase="job", context={"attempt": 0}))

        runtime.finalize(phase="stage")

        watch_events = [ev for ev in events.events if getattr(ev, "component", None) == "watch"]
        self.assertEqual(len(watch_events), 1)
        self.assertTrue(runtime.abort_requested)
        self.assertIsNotNone(runtime.failure_reason)
        self.assertIn("missing sequence", runtime.failure_reason or "")
        self.assertEqual(getattr(watch_events[0], "status", None), "fail")

    def test_dispatcher_aborts_remaining_jobs_on_watch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_ctx(
                td,
                watch={
                    "sources": [
                        {
                            "name": "session_out",
                            "type": "session",
                            "stream": "stdout",
                        }
                    ],
                    "watchers": [
                        {
                            "name": "fail_on_traceback",
                            "source": "session_out",
                            "when": {
                                "match": "Traceback",
                            },
                            "then": {
                                "fail": "immediate",
                            },
                        }
                    ],
                },
            )
            dispatcher = LocalDispatcher()
            executor = _FakeExecutor()
            plan = [
                JobPlan(job_id="job-001", items=[TestItem(id="step-1")]),
                JobPlan(job_id="job-002", items=[TestItem(id="step-2")]),
            ]

            def make_cmd(job: JobPlan, attempt: int, host_reports_dir: str) -> ExecutableCommand:
                return ExecutableCommand(
                    cmd=["bash", "-lc", "echo hello"],
                    env={},
                    workdir=".",
                    workdir_repo=True,
                    artifacts_dir=ctx.stage_reports_dir,
                    artifacts_root="/artifacts",
                    timeout_seconds=5,
                )

            result = dispatcher.run_jobs(
                ctx=ctx,
                plan=plan,
                make_cmd=make_cmd,
                executor_adapter=executor,
                watch=ctx.watch,
            )

            self.assertEqual(len(executor.calls), 1)
            self.assertEqual(result.failed_job_ids, ["job-001", "job-002"])
            self.assertTrue(any(job.timeout_scope == "watch" for job in result.results))

    def test_http_watch_reports_only_once_for_repeated_failure_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            events = _RecordingEvents([])
            runtime = WatchRuntime(
                {
                    "sources": [
                        {
                            "name": "api_health",
                            "type": "http",
                            "url": "http://127.0.0.1:8080/health",
                            "mode": "poll",
                            "interval_seconds": 1,
                            "phases": ["stage"],
                        }
                    ],
                    "watchers": [
                        {
                            "name": "report_on_degraded",
                            "source": "api_health",
                            "when": {
                                "status": 500,
                                "match": "degraded",
                            },
                            "then": {
                                "report": {
                                    "message": "HTTP health reported a degraded state",
                                },
                            },
                        }
                    ],
                },
                events=events,
                base_dir=Path(td),
                run_scope={"run_id": "run-001", "stage_id": "stage-1"},
            )

            with patch("testfabric.watch.runtime.urllib.request.urlopen") as urlopen:
                urlopen.return_value = _FakeHTTPResponse(status=500, body=b"service degraded\n")
                runtime.poll_once(phase="stage")
                runtime.poll_once(phase="stage")

            watch_events = [ev for ev in events.events if getattr(ev, "component", None) == "watch"]
            self.assertEqual(len(watch_events), 1)
            self.assertEqual(runtime.reports[0]["watcher"], "report_on_degraded")
            self.assertEqual(runtime.reports[0]["status_code"], 500)
            self.assertEqual(runtime.reports[0]["message"], "HTTP health reported a degraded state")

    def test_metric_watch_reports_only_once_for_repeated_threshold_crossing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            events = _RecordingEvents([])
            runtime = WatchRuntime(
                {
                    "sources": [
                        {
                            "name": "cpu_usage",
                            "type": "metric",
                            "mode": "poll",
                            "interval_seconds": 1,
                            "shell": 'echo "95.5"',
                            "phases": ["stage"],
                        }
                    ],
                    "watchers": [
                        {
                            "name": "cpu_over_90",
                            "source": "cpu_usage",
                            "when": {
                                "gt": 90,
                            },
                            "then": {
                                "report": {
                                    "message": "CPU usage crossed 90%",
                                },
                            },
                        }
                    ],
                },
                events=events,
                base_dir=Path(td),
                run_scope={"run_id": "run-001", "stage_id": "stage-1"},
            )

            runtime.poll_once(phase="stage")
            runtime.poll_once(phase="stage")

            watch_events = [ev for ev in events.events if getattr(ev, "component", None) == "watch"]
            self.assertEqual(len(watch_events), 1)
            self.assertEqual(runtime.reports[0]["watcher"], "cpu_over_90")
            self.assertEqual(runtime.reports[0]["message"], "CPU usage crossed 90%")
            self.assertEqual(runtime.reports[0]["match"], "95.5")


if __name__ == "__main__":
    unittest.main()
