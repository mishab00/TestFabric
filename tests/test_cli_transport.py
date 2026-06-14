from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from typer.testing import CliRunner

from testfabric.cli.config import CONFIG_ENV, CLIConfig, ContextProfile, save_config
from testfabric.cli.main import app
from testfabric.cli.transport import HttpTransport, RunDispatchResult, RunInvocation
from testfabric.core.contracts import ReplayRequest, RunQuery, WorkerStatus
from testfabric.spec.schema import RunSpec
from testfabric.worker import WorkerServeSummary


def _write_minimal_spec(spec_path: Path) -> None:
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


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class CliTransportTests(unittest.TestCase):
    def test_run_uses_direct_transport_for_local_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            spec_path = root / "run.yaml"
            _write_minimal_spec(spec_path)

            env = {CONFIG_ENV: str(config_path)}
            fake_result = RunDispatchResult(
                transport="direct",
                run_id="local-123",
                ok=True,
                accepted=False,
                payload={
                    "ok": True,
                    "run_id": "local-123",
                    "run_dir": str(root / "artifacts" / "runs" / "local-123"),
                    "run_summary_path": str(root / "artifacts" / "runs" / "local-123" / "summary.json"),
                },
            )

            with patch("testfabric.cli.transport.DirectTransport.execute", return_value=fake_result) as execute:
                result = runner.invoke(app, ["run", str(spec_path), "--json"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_called_once()
        self.assertIn('"run_id": "local-123"', result.output)
        self.assertIn('"transport": "direct"', result.output)

    def test_run_uses_http_transport_for_remote_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            spec_path = root / "run.yaml"
            _write_minimal_spec(spec_path)

            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            fake_result = RunDispatchResult(
                transport="http",
                run_id="remote-123",
                ok=True,
                accepted=True,
                payload={"run_id": "remote-123", "accepted": True, "message": "queued"},
                message="queued",
                status_code=202,
            )

            with patch("testfabric.cli.transport.HttpTransport.execute", return_value=fake_result) as execute:
                result = runner.invoke(app, ["run", str(spec_path), "--json"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_called_once()
        self.assertIn('"run_id": "remote-123"', result.output)
        self.assertIn('"transport": "http"', result.output)

    def test_runs_lists_remote_history_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "total": 1,
                "items": [
                    {
                        "run_id": "remote-123",
                        "accepted": True,
                        "message": "queued",
                        "status": {
                            "run_id": "remote-123",
                            "state": "queued",
                            "message": "queued",
                            "metadata": {"team": "platform", "project": "api-gateway"},
                        },
                    }
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.list_runs", return_value=payload) as list_runs:
                result = runner.invoke(app, ["runs"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        list_runs.assert_called_once()
        self.assertIn("total: 1", result.output)
        self.assertIn("remote-123", result.output)
        self.assertIn("api-gateway", result.output)

    def test_runs_applies_filters_and_queries_remote_backend(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {"total": 1, "items": []}

            with patch("testfabric.cli.transport.HttpTransport.list_runs", return_value=payload) as list_runs:
                result = runner.invoke(
                    app,
                    [
                        "runs",
                        "--team",
                        "platform",
                        "--project",
                        "api-gateway",
                        "--status",
                        "completed",
                        "--tag",
                        "nightly",
                        "--limit",
                        "10",
                    ],
                    env=env,
                )

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = list_runs.call_args
        self.assertIsInstance(args[0], RunQuery)
        self.assertEqual(args[0].team, "platform")
        self.assertEqual(args[0].project, "api-gateway")
        self.assertEqual(args[0].status, ["completed"])
        self.assertEqual(args[0].tags, ["nightly"])
        self.assertEqual(args[0].limit, 10)
        self.assertIn("filters: team=platform", result.output)
        self.assertIn("limit=10", result.output)

    def test_dashboard_displays_remote_overview_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "total": 3,
                "counts_by_state": {"completed": 1, "running": 1, "queued": 1},
                "counts_by_verdict": {"PASSED": 1, "-": 2},
                "counts_by_team": {"platform": 2, "infra": 1},
                "counts_by_project": {"api-gateway": 2, "deploy": 1},
                "recent_runs": [
                    {
                        "run_id": "run-003",
                        "state": "queued",
                        "verdict": None,
                        "team": "infra",
                        "project": "deploy",
                        "duration_seconds": None,
                        "message": "accepted for execution",
                    },
                    {
                        "run_id": "run-002",
                        "state": "running",
                        "verdict": None,
                        "team": "platform",
                        "project": "api-gateway",
                        "duration_seconds": 3.0,
                        "message": "claimed for execution",
                    },
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.dashboard", return_value=payload) as dashboard:
                result = runner.invoke(app, ["dashboard", "--team", "platform", "--limit", "2"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = dashboard.call_args
        self.assertIsInstance(args[0], RunQuery)
        self.assertEqual(args[0].team, "platform")
        self.assertEqual(args[0].limit, 2)
        self.assertIn("total: 3", result.output)
        self.assertIn("states: queued=1, running=1, completed=1", result.output)
        self.assertIn("teams: infra=1, platform=2", result.output)
        self.assertIn("recent runs:", result.output)
        self.assertIn("run-003", result.output)

    def test_show_displays_remote_run_details_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "accepted": True,
                "message": "queued",
                "duration_seconds": 12.5,
                "team": "platform",
                "project": "api-gateway",
                "workspace": "/workspaces/api-gateway",
                "context": "team-staging",
                "branch": "main",
                "ref": "main",
                "tags": ["nightly"],
                "links": {"self": "/runs/remote-123"},
                "request": {
                    "mode": "run",
                    "spec_path": "run.yaml",
                    "repo_url": "git@example.com:org/repo.git",
                    "ref": "main",
                    "team": "platform",
                    "project": "api-gateway",
                    "workspace": "/workspaces/api-gateway",
                    "context": "team-staging",
                    "inputs": {"env": "staging"},
                    "tags": ["nightly"],
                    "metadata": {"triggered_by": "alice"},
                },
                "status": {
                    "run_id": "remote-123",
                    "state": "running",
                    "verdict": None,
                    "message": "claimed for execution",
                    "started_at": "2026-04-13T10:00:00+00:00",
                    "progress": {"running": True},
                    "metadata": {"team": "platform", "project": "api-gateway", "context": "team-staging"},
                },
                "result": {
                    "run_id": "remote-123",
                    "ok": True,
                    "state": "completed",
                    "verdict": "PASSED",
                    "run_dir": "/tmp/testfabric/run-remote-123",
                    "summary_path": "/tmp/testfabric/run-remote-123/summary.json",
                    "metadata": {"worker_id": "worker-a"},
                },
                "summary": {
                    "run": {"run_id": "remote-123", "verdict": "PASSED"},
                },
            }

            with patch("testfabric.cli.transport.HttpTransport.get_run_detail", return_value=payload) as get_run_detail:
                result = runner.invoke(app, ["show", "remote-123"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        get_run_detail.assert_called_once_with("remote-123")
        self.assertIn("run_id:   remote-123", result.output)
        self.assertIn("state:    running", result.output)
        self.assertIn("project:  api-gateway", result.output)
        self.assertIn("duration: 00:00:12", result.output)
        self.assertIn("Request", result.output)
        self.assertIn("Status", result.output)
        self.assertIn("Result", result.output)
        self.assertIn("Summary", result.output)
        self.assertIn("links:", result.output)

    def test_stages_jobs_and_job_details_render_remote_run_summary(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "summary": {
                    "stages": [
                        {
                            "stage_id": "01-build",
                            "stage_title": "build",
                            "verdict": "PASSED",
                            "executor": "docker",
                            "transport": {"type": "ssh"},
                            "duration_seconds": 5.5,
                            "jobs_total": 2,
                            "jobs_failed": 0,
                            "target_id": "staging",
                            "target_name": "staging-a",
                            "target_kind": "remote",
                            "artifacts": {"files_total": 3, "primary": ["logs/build.log"]},
                            "jobs": [
                                {
                                    "job_id": "job-0001",
                                    "title": "setup",
                                    "verdict": "PASSED",
                                    "attempt": 0,
                                    "duration_seconds": 1.2,
                                    "transport": {"type": "ssh"},
                                    "target_id": "staging",
                                    "message": "done",
                                },
                                {
                                    "job_id": "job-0002",
                                    "title": "test",
                                    "verdict": "PASSED",
                                    "attempt": 0,
                                    "duration_seconds": 4.3,
                                    "transport": {"type": "ssh"},
                                    "target_id": "staging",
                                    "message": "done",
                                },
                            ],
                        }
                    ]
                },
            }

            with patch("testfabric.cli.transport.HttpTransport.get_run_detail", return_value=payload) as get_run_detail:
                stages_result = runner.invoke(app, ["stages", "remote-123"], env=env)
                stage_result = runner.invoke(app, ["stage", "remote-123", "01-build"], env=env)
                jobs_result = runner.invoke(app, ["jobs", "remote-123", "01-build"], env=env)
                job_result = runner.invoke(app, ["job", "remote-123", "01-build", "job-0002"], env=env)

        self.assertEqual(stages_result.exit_code, 0, stages_result.output)
        self.assertEqual(stage_result.exit_code, 0, stage_result.output)
        self.assertEqual(jobs_result.exit_code, 0, jobs_result.output)
        self.assertEqual(job_result.exit_code, 0, job_result.output)
        self.assertGreaterEqual(get_run_detail.call_count, 4)
        self.assertIn("stage_id", stages_result.output)
        self.assertIn("01-build", stages_result.output)
        self.assertIn("Jobs", stage_result.output)
        self.assertIn("job-0002", jobs_result.output)
        self.assertIn("attempt:  0", job_result.output)
        self.assertIn("transport:ssh", job_result.output)

    def test_artifacts_displays_remote_artifact_browser_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "path": ".",
                "kind": "tree",
                "total": 2,
                "next_offset": 2,
                "items": [
                    {
                        "path": "logs",
                        "name": "logs",
                        "kind": "dir",
                        "depth": 0,
                        "size_bytes": None,
                        "modified_at": "2026-04-13T10:00:00+00:00",
                    },
                    {
                        "path": "logs/build.log",
                        "name": "build.log",
                        "kind": "file",
                        "depth": 1,
                        "size_bytes": 12,
                        "modified_at": "2026-04-13T10:00:01+00:00",
                    },
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.list_run_artifacts", return_value=payload) as list_artifacts:
                result = runner.invoke(app, ["artifacts", "remote-123"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        list_artifacts.assert_called_once()
        args, kwargs = list_artifacts.call_args
        self.assertEqual(args[0], "remote-123")
        self.assertEqual(kwargs["limit"], 200)
        self.assertIn("run_id: remote-123", result.output)
        self.assertIn("kind:   tree", result.output)
        self.assertIn("logs/build.log", result.output)

    def test_artifacts_supports_search_across_remote_text_files(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "path": ".",
                "kind": "search",
                "query": "needle",
                "total": 1,
                "next_offset": 1,
                "items": [
                    {
                        "path": "logs/build.log",
                        "name": "build.log",
                        "kind": "match",
                        "line_number": 2,
                        "line": "needle line",
                        "modified_at": "2026-04-13T10:00:00+00:00",
                        "size_bytes": 12,
                    }
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.list_run_artifacts", return_value=payload) as list_artifacts:
                result = runner.invoke(app, ["artifacts", "remote-123", "--search", "needle"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = list_artifacts.call_args
        self.assertEqual(args[0], "remote-123")
        self.assertEqual(kwargs["search"], "needle")
        self.assertIn("kind:   search", result.output)
        self.assertIn("needle line", result.output)

    def test_logs_displays_remote_log_tree_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "path": "logs",
                "kind": "tree",
                "total": 2,
                "next_offset": 2,
                "items": [
                    {
                        "path": "logs/stdout.log",
                        "name": "stdout.log",
                        "kind": "file",
                        "depth": 0,
                        "size_bytes": 12,
                        "modified_at": "2026-04-13T10:00:00+00:00",
                    },
                    {
                        "path": "logs/stderr.log",
                        "name": "stderr.log",
                        "kind": "file",
                        "depth": 0,
                        "size_bytes": 4,
                        "modified_at": "2026-04-13T10:00:00+00:00",
                    },
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.list_run_artifacts", return_value=payload) as list_artifacts:
                result = runner.invoke(app, ["logs", "remote-123"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = list_artifacts.call_args
        self.assertEqual(args[0], "remote-123")
        self.assertEqual(kwargs["path"], "logs")
        self.assertTrue(kwargs["recursive"])
        self.assertEqual(kwargs["offset"], 0)
        self.assertEqual(kwargs["limit"], 200)
        self.assertIsNone(kwargs["search"])
        self.assertIn("log_path: logs", result.output)
        self.assertIn("stdout.log", result.output)
        self.assertIn("stderr.log", result.output)

    def test_logs_supports_search_across_remote_log_files(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "path": "logs",
                "kind": "search",
                "query": "needle",
                "total": 1,
                "next_offset": 1,
                "items": [
                    {
                        "path": "logs/stdout.log",
                        "name": "stdout.log",
                        "kind": "match",
                        "line_number": 3,
                        "line": "needle line",
                        "modified_at": "2026-04-13T10:00:00+00:00",
                        "size_bytes": 12,
                    }
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.list_run_artifacts", return_value=payload) as list_artifacts:
                result = runner.invoke(app, ["logs", "remote-123", "--search", "needle"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = list_artifacts.call_args
        self.assertEqual(args[0], "remote-123")
        self.assertEqual(kwargs["search"], "needle")
        self.assertIn("search: needle", result.output)
        self.assertIn("needle line", result.output)

    def test_events_streams_remote_run_events_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "path": "/tmp/testfabric/run-remote-123/events.jsonl",
                "total": 2,
                "next_offset": 2,
                "items": [
                    {
                        "seq": 1,
                        "ts": "2026-04-13T10:00:00Z",
                        "component": "run",
                        "action": "start",
                        "status": "start",
                        "message": "run:start start",
                    },
                    {
                        "seq": 2,
                        "ts": "2026-04-13T10:00:01Z",
                        "component": "job",
                        "action": "start",
                        "status": "start",
                        "message": "job:start start",
                    },
                ],
            }

            with patch("testfabric.cli.transport.HttpTransport.get_run_events", return_value=payload) as get_events:
                result = runner.invoke(app, ["events", "remote-123"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        get_events.assert_called_once()
        args, kwargs = get_events.call_args
        self.assertEqual(args[0], "remote-123")
        self.assertEqual(kwargs["offset"], 0)
        self.assertEqual(kwargs["limit"], 50)
        self.assertIn("run_id: remote-123", result.output)
        self.assertIn("total: 2", result.output)
        self.assertIn("run:start start", result.output)
        self.assertIn("job:start start", result.output)

    def test_events_follow_requests_follow_mode_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "path": "/tmp/testfabric/run-remote-123/events.jsonl",
                "total": 1,
                "next_offset": 1,
                "state": "running",
                "follow": True,
                "items": [
                    {
                        "seq": 1,
                        "ts": "2026-04-13T10:00:01Z",
                        "component": "job",
                        "action": "start",
                        "status": "start",
                        "message": "job:start start",
                    },
                ],
            }
            terminal_payload = {
                "run_id": "remote-123",
                "path": "/tmp/testfabric/run-remote-123/events.jsonl",
                "total": 1,
                "next_offset": 1,
                "state": "completed",
                "follow": True,
                "items": [],
            }

            with patch(
                "testfabric.cli.transport.HttpTransport.get_run_events",
                side_effect=[payload, terminal_payload],
            ) as get_events:
                result = runner.invoke(app, ["events", "remote-123", "--follow"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = get_events.call_args
        self.assertEqual(args[0], "remote-123")
        self.assertTrue(kwargs["follow"])
        self.assertIn("job:start start", result.output)

    def test_cancel_posts_to_remote_backend_and_reports_state(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "run_id": "remote-123",
                "accepted": True,
                "message": "manual stop",
                "status": {
                    "run_id": "remote-123",
                    "state": "canceled",
                    "verdict": "CANCELED",
                    "message": "manual stop",
                    "metadata": {"worker_id": "worker-a", "canceled_reason": "manual stop"},
                },
                "links": {"self": "/runs/remote-123"},
            }

            with patch("testfabric.cli.transport.HttpTransport.cancel_run", return_value=payload) as cancel_run:
                result = runner.invoke(app, ["cancel", "remote-123", "--reason", "manual stop"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        cancel_run.assert_called_once_with("remote-123", reason="manual stop")
        self.assertIn("backend: http://localhost:5678", result.output)
        self.assertIn("context: team-staging", result.output)
        self.assertIn("state:    canceled", result.output)
        self.assertIn("verdict:  CANCELED", result.output)
        self.assertIn("reason:   manual stop", result.output)

    def test_compare_displays_remote_run_differences_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            payload = {
                "left_run_id": "run-001",
                "right_run_id": "run-002",
                "summary": {
                    "same_state": False,
                    "same_verdict": False,
                    "same_team": True,
                    "same_project": True,
                    "duration_delta_seconds": 10.0,
                },
                "fields": [
                    {"field": "state", "left": "completed", "right": "failed", "same": False},
                    {"field": "verdict", "left": "PASSED", "right": "FAILED", "same": False},
                ],
                "left": {
                    "run_id": "run-001",
                    "state": "completed",
                    "duration_seconds": 30.0,
                    "metadata": {"team": "platform", "project": "api-gateway", "context": "team-staging"},
                    "tags": ["nightly"],
                    "status": {"verdict": "PASSED"},
                },
                "right": {
                    "run_id": "run-002",
                    "state": "failed",
                    "duration_seconds": 40.0,
                    "metadata": {"team": "platform", "project": "api-gateway", "context": "team-staging"},
                    "tags": ["nightly", "retry"],
                    "status": {"verdict": "FAILED"},
                },
            }

            with patch("testfabric.cli.transport.HttpTransport.compare_runs", return_value=payload) as compare_runs:
                result = runner.invoke(app, ["compare", "run-001", "run-002"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        compare_runs.assert_called_once_with("run-001", "run-002")
        self.assertIn("left:  run-001", result.output)
        self.assertIn("right: run-002", result.output)
        self.assertIn("duration_delta_seconds=10.0", result.output)
        self.assertIn("PASSED", result.output)
        self.assertIn("FAILED", result.output)

    def test_replay_posts_to_remote_backend_and_reports_new_run(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            fake_result = RunDispatchResult(
                transport="http",
                run_id="run-replay-001",
                ok=True,
                accepted=True,
                payload={
                    "run_id": "run-replay-001",
                    "accepted": True,
                    "message": "replayed",
                    "status": {
                        "run_id": "run-replay-001",
                        "state": "queued",
                        "metadata": {"replayed_from": "run-001", "replay_mode": "exact"},
                    },
                },
                message="replayed",
                status_code=202,
            )

            with patch("testfabric.cli.transport.HttpTransport.replay_run", return_value=fake_result) as replay_run:
                result = runner.invoke(app, ["replay", "run-001", "--mode", "exact"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        replay_run.assert_called_once()
        self.assertIn("run_id:   run-replay-001", result.output)
        self.assertIn("replayed_from", result.output)
        self.assertIn("exact", result.output)

    def test_worker_serve_uses_remote_backend_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                        workspace="/workspaces/api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            fake_runtime = Mock()
            fake_runtime.serve.return_value = WorkerServeSummary(
                loops=3,
                runs_claimed=2,
                runs_completed=1,
                runs_failed=1,
                idle_polls=1,
                stopped_reason="idle",
            )

            with patch("testfabric.cli.main.RemoteWorkerRuntime", return_value=fake_runtime) as runtime_cls:
                result = runner.invoke(
                    app,
                    [
                        "worker",
                        "serve",
                        "--worker-id",
                        "worker-a",
                        "--label",
                        "linux",
                        "--label",
                        "docker",
                        "--policy",
                        "priority",
                        "--max-runs",
                        "2",
                        "--max-idle-polls",
                        "1",
                        "--idle-sleep",
                        "0",
                        "--heartbeat-interval",
                        "0",
                    ],
                    env=env,
                )

        self.assertEqual(result.exit_code, 0, result.output)
        runtime_cls.assert_called_once()
        constructor_kwargs = runtime_cls.call_args.kwargs
        self.assertEqual(constructor_kwargs["client"].api_url, "http://localhost:5678")
        self.assertEqual(constructor_kwargs["client"].token, "secret")
        self.assertEqual(constructor_kwargs["worker"].worker_id, "worker-a")
        self.assertEqual(constructor_kwargs["worker"].labels, ["linux", "docker"])
        fake_runtime.serve.assert_called_once_with(
            worker_id="worker-a",
            worker_labels=["linux", "docker"],
            policy="priority",
            max_runs=2,
            max_idle_polls=1,
            idle_sleep_seconds=0.0,
        )
        self.assertIn("backend: http://localhost:5678", result.output)
        self.assertIn("context: team-staging", result.output)
        self.assertIn("worker:  worker-a", result.output)
        self.assertIn("claimed: 2", result.output)
        self.assertIn("stopped: idle", result.output)

    def test_worker_serve_rejects_local_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.yaml"
            save_config(CLIConfig(active_context="local", contexts={"local": ContextProfile(name="local", mode="local")}), config_path)

            result = runner.invoke(app, ["worker", "serve"], env={CONFIG_ENV: str(config_path)})

        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("worker serve requires a remote backend context", result.output)

    def test_worker_list_displays_remote_workers_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            fake_client = Mock()
            fake_client.list_workers.return_value = [
                WorkerStatus(
                    worker_id="worker-a",
                    mode="remote",
                    host="1.1.1.1",
                    user="root",
                    labels=["linux", "docker"],
                    capacity=2,
                    active_leases=1,
                    health_state="healthy",
                    health_reasons=["disk_ok"],
                    last_heartbeat_at="2026-04-13T10:00:00+00:00",
                    created_at="2026-04-13T09:00:00+00:00",
                    updated_at="2026-04-13T10:00:00+00:00",
                    metadata={"region": "us-east"},
                )
            ]

            with patch("testfabric.cli.main.WorkerClient", return_value=fake_client) as worker_client:
                result = runner.invoke(app, ["worker", "list"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        worker_client.assert_called_once_with(api_url="http://localhost:5678", token="secret")
        fake_client.list_workers.assert_called_once_with()
        self.assertIn("backend: http://localhost:5678", result.output)
        self.assertIn("worker-a", result.output)
        self.assertIn("healthy", result.output)
        self.assertIn("1/2", result.output)
        self.assertIn("linux, docker", result.output)

    def test_worker_status_displays_remote_worker_details_from_active_context(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.yaml"
            cfg = CLIConfig(
                active_context="team-staging",
                contexts={
                    "local": ContextProfile(name="local", mode="local"),
                    "team-staging": ContextProfile(
                        name="team-staging",
                        mode="remote",
                        api_url="http://localhost:5678",
                        token="secret",
                        team="platform",
                        project="api-gateway",
                    ),
                },
            )
            save_config(cfg, config_path)

            env = {CONFIG_ENV: str(config_path)}
            fake_client = Mock()
            fake_client.get_worker.return_value = WorkerStatus(
                worker_id="worker-a",
                mode="remote",
                host="1.1.1.1",
                user="root",
                labels=["linux", "docker"],
                capacity=2,
                active_leases=1,
                health_state="healthy",
                health_reasons=["disk_ok"],
                last_heartbeat_at="2026-04-13T10:00:00+00:00",
                created_at="2026-04-13T09:00:00+00:00",
                updated_at="2026-04-13T10:00:00+00:00",
                metadata={"region": "us-east"},
            )

            with patch("testfabric.cli.main.WorkerClient", return_value=fake_client) as worker_client:
                result = runner.invoke(app, ["worker", "status", "worker-a"], env=env)

        self.assertEqual(result.exit_code, 0, result.output)
        worker_client.assert_called_once_with(api_url="http://localhost:5678", token="secret")
        fake_client.get_worker.assert_called_once_with("worker-a")
        self.assertIn("worker_id: worker-a", result.output)
        self.assertIn("health: healthy", result.output)
        self.assertIn("leases: 1/2", result.output)
        self.assertIn("labels: linux, docker", result.output)
        self.assertIn('"region": "us-east"', result.output)

    def test_http_transport_posts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            _write_minimal_spec(spec_path)
            spec = RunSpec.load(str(spec_path))

            context = ContextProfile(
                name="team-staging",
                mode="remote",
                api_url="http://localhost:5678",
                token="XXXX",
                team="platform",
                project="api-gateway",
                workspace="/workspaces/api-gateway",
            )
            invocation = RunInvocation(
                spec_path=spec_path,
                spec_text=spec_path.read_text(encoding="utf-8"),
                spec=spec,
                resolved_inputs={"env": "staging"},
                mode="run",
                run_id="remote-456",
                suite="smoke",
                build=True,
                verbosity="normal",
                profile="remote",
                context=context,
                lint_spec=True,
            )

            fake_response = _FakeHTTPResponse(
                202,
                json.dumps(
                    {
                        "run_id": "remote-456",
                        "accepted": True,
                        "message": "queued",
                    }
                ).encode("utf-8"),
            )

            with patch("testfabric.cli.transport.urlopen", return_value=fake_response) as urlopen:
                result = HttpTransport(context).execute(invocation)

        self.assertEqual(result.transport, "http")
        self.assertEqual(result.run_id, "remote-456")
        self.assertTrue(result.accepted)
        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.message, "queued")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://localhost:5678/runs")
        self.assertEqual(request.get_header("Authorization"), "Bearer XXXX")
        self.assertEqual(body["run_id"], "remote-456")
        self.assertEqual(body["mode"], "run")
        self.assertEqual(body["context"], "team-staging")
        self.assertEqual(body["team"], "platform")
        self.assertEqual(body["project"], "api-gateway")
        self.assertEqual(body["workspace"], "/workspaces/api-gateway")
        self.assertEqual(body["inputs"]["env"], "staging")
        self.assertEqual(body["metadata"]["suite"], "smoke")
        self.assertTrue(body["metadata"]["build"])
        self.assertTrue(body["metadata"]["lint_spec"])
        self.assertEqual(body["metadata"]["profile"], "remote")
        self.assertEqual(body["tags"], ["context:team-staging", "mode:run", "suite:smoke", "lint-spec", "build"])
        self.assertIn("pipeline", body["spec"])
        self.assertIn("spec_yaml", body)

    def test_http_transport_gets_runs_and_single_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            context = ContextProfile(
                name="team-staging",
                mode="remote",
                api_url="http://localhost:5678",
                token="XXXX",
                team="platform",
                project="api-gateway",
            )

            list_response = _FakeHTTPResponse(
                200,
                json.dumps(
                    {
                        "total": 1,
                        "items": [
                            {
                                "run_id": "run-001",
                                "accepted": True,
                                "status": {
                                    "run_id": "run-001",
                                    "state": "queued",
                                    "metadata": {"team": "platform"},
                                },
                            }
                        ],
                    }
                ).encode("utf-8"),
            )
            get_response = _FakeHTTPResponse(
                200,
                json.dumps(
                    {
                        "run_id": "run-001",
                        "accepted": True,
                        "status": {
                            "run_id": "run-001",
                            "state": "running",
                            "metadata": {"team": "platform"},
                        },
                    }
                ).encode("utf-8"),
            )

            with patch("testfabric.cli.transport.urlopen", side_effect=[list_response, get_response]) as urlopen:
                transport = HttpTransport(context)
                listed = transport.list_runs()
                fetched = transport.get_run("run-001")

        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["run_id"], "run-001")
        self.assertEqual(fetched["run_id"], "run-001")
        self.assertEqual(urlopen.call_args_list[0].args[0].full_url, "http://localhost:5678/runs")
        self.assertEqual(urlopen.call_args_list[1].args[0].full_url, "http://localhost:5678/runs/run-001")

    def test_http_transport_encodes_run_filters_in_query_string(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            context = ContextProfile(
                name="team-staging",
                mode="remote",
                api_url="http://localhost:5678",
                token="XXXX",
                team="platform",
                project="api-gateway",
            )

            list_response = _FakeHTTPResponse(200, json.dumps({"total": 0, "items": []}).encode("utf-8"))
            query = RunQuery(team="platform", project="api-gateway", status=["completed"], tags=["nightly"], limit=10)

            with patch("testfabric.cli.transport.urlopen", return_value=list_response) as urlopen:
                transport = HttpTransport(context)
                transport.list_runs(query)

        request = urlopen.call_args.args[0]
        self.assertIn("team=platform", request.full_url)
        self.assertIn("project=api-gateway", request.full_url)
        self.assertIn("status=completed", request.full_url)
        self.assertIn("tags=nightly", request.full_url)
        self.assertIn("limit=10", request.full_url)

    def test_http_transport_compare_and_replay_requests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            context = ContextProfile(
                name="team-staging",
                mode="remote",
                api_url="http://localhost:5678",
                token="XXXX",
                team="platform",
                project="api-gateway",
            )

            compare_response = _FakeHTTPResponse(
                200,
                json.dumps(
                    {
                        "left_run_id": "run-001",
                        "right_run_id": "run-002",
                        "summary": {"same_state": False},
                        "fields": [],
                    }
                ).encode("utf-8"),
            )
            replay_response = _FakeHTTPResponse(
                202,
                json.dumps(
                    {
                        "run_id": "run-replay-001",
                        "accepted": True,
                        "message": "replayed",
                        "status": {
                            "run_id": "run-replay-001",
                            "state": "queued",
                            "metadata": {"replayed_from": "run-001"},
                        },
                    }
                ).encode("utf-8"),
            )

            with patch("testfabric.cli.transport.urlopen", side_effect=[compare_response, replay_response]) as urlopen:
                transport = HttpTransport(context)
                compare_payload = transport.compare_runs("run-001", "run-002")
                replay_payload = transport.replay_run(ReplayRequest(run_id="run-001", mode="exact"))

        self.assertEqual(compare_payload["left_run_id"], "run-001")
        self.assertEqual(compare_payload["right_run_id"], "run-002")
        self.assertEqual(replay_payload.run_id, "run-replay-001")
        self.assertTrue(replay_payload.accepted)
        self.assertEqual(urlopen.call_args_list[0].args[0].full_url, "http://localhost:5678/compare/run-001/run-002")
        self.assertEqual(urlopen.call_args_list[1].args[0].full_url, "http://localhost:5678/runs/run-001/replay")


if __name__ == "__main__":
    unittest.main()
