"""
Micro-package showcase tests.

Each section demonstrates 2–3 essential features of a testfabric micro-package,
proving that the capability can be imported and used independently.

Run with:
    pytest tests/test_micropackage_showcase.py -v
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


# ════════════════════════════════════════════════════════════════════════════════
# 1.  testfabric.core  — Events, Contracts, Redaction
# ════════════════════════════════════════════════════════════════════════════════


class CoreEventsShowcase(unittest.TestCase):
    """Show that the event system works standalone with no external deps."""

    def test_null_events_accepts_any_event_silently(self) -> None:
        """NullEvents is a drop-in sink that swallows everything."""
        from testfabric.core import Event, NullEvents

        sink = NullEvents()
        sink.emit(Event("build", "start", "ok", {"image": "python:3.11"}))
        sink.stream("logs/build.log", "line 1\nline 2\n")
        # No exception — NullEvents is the "zero-cost" default.

    def test_file_events_writes_jsonl_and_streams_text(self) -> None:
        """FileEvents persists structured events + arbitrary text streams."""
        from testfabric.core import Event, FileEvents

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run-001"
            sink = FileEvents(run_dir, echo=False, run_id="run-001")

            sink.emit(Event("stage", "start", "ok", {"index": 0}))
            sink.emit(Event("job", "end", "fail", {"exit_code": 1}))
            sink.stream("logs/stdout.log", "hello world\n")

            events = (run_dir / "events.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(events), 2)
            first = json.loads(events[0])
            self.assertEqual(first["component"], "stage")
            self.assertEqual(first["run_id"], "run-001")

            streamed = (run_dir / "logs" / "stdout.log").read_text()
            self.assertIn("hello world", streamed)

    def test_event_sink_protocol_allows_custom_implementations(self) -> None:
        """Any object with .emit() and .stream() satisfies EventSink."""
        from testfabric.core import Event, EventSink

        class MemorySink:
            def __init__(self) -> None:
                self.events: list[Event] = []
                self.streams: list[str] = []

            def emit(self, event: Event) -> None:
                self.events.append(event)

            def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None:
                self.streams.append(text)

        sink = MemorySink()
        sink.emit(Event("test", "run", "pass"))
        sink.stream("out.log", "output\n")

        self.assertEqual(len(sink.events), 1)
        self.assertEqual(sink.events[0].component, "test")
        self.assertEqual(sink.streams, ["output\n"])


class CoreContractsShowcase(unittest.TestCase):
    """Show pydantic contract models for run submissions & results."""

    def test_run_request_round_trip(self) -> None:
        """RunRequest validates input and serialises cleanly."""
        from testfabric.core import RunRequest

        req = RunRequest(
            run_id="r-01",
            spec_path="run.yaml",
            team="backend",
            inputs={"env": "staging"},
            tags=["nightly"],
        )
        data = req.model_dump()
        self.assertEqual(data["run_id"], "r-01")
        self.assertEqual(data["inputs"]["env"], "staging")

        restored = RunRequest.model_validate(data)
        self.assertEqual(restored.run_id, "r-01")

    def test_run_result_captures_verdict(self) -> None:
        """RunResult carries the outcome of a completed run."""
        from testfabric.core import RunResult

        result = RunResult(run_id="r-01", ok=False, verdict="FAILED", error="timeout")
        self.assertFalse(result.ok)
        self.assertEqual(result.verdict, "FAILED")
        self.assertEqual(result.error, "timeout")


class CoreRedactionShowcase(unittest.TestCase):
    """Show secret redaction for text and nested data structures."""

    def test_redact_exact_values_in_text(self) -> None:
        from testfabric.core import SecretRedactor

        redactor = SecretRedactor.from_values(("mypassword",))
        self.assertEqual(
            redactor.redact_text("token=mypassword rest"),
            "token=*** rest",
        )

    def test_redact_field_names_in_nested_dicts(self) -> None:
        from testfabric.core import SecretRedactor

        redactor = SecretRedactor.from_values(
            field_names=("password", "token"),
        )
        data = {"user": "alice", "password": "hunter2", "nested": {"token": "abc"}}
        clean = redactor.redact_data(data)
        self.assertEqual(clean["user"], "alice")
        self.assertEqual(clean["password"], "***")
        self.assertEqual(clean["nested"]["token"], "***")

    def test_redact_text_convenience_function(self) -> None:
        from testfabric.core import redact_text

        out = redact_text("secret=abc123 rest", ("abc123",))
        self.assertEqual(out, "secret=*** rest")


class CoreEnvHelpersShowcase(unittest.TestCase):
    """Show env merge and variable expansion."""

    def test_merge_env_layers(self) -> None:
        from testfabric.core import merge_env

        result = merge_env(
            {"A": "1", "B": "2"},
            {"B": "override", "C": "3"},
        )
        self.assertEqual(result, {"A": "1", "B": "override", "C": "3"})

    def test_expand_vars_in_strings(self) -> None:
        from testfabric.core import expand_vars

        out = expand_vars("Hello $USER at ${HOME}/work", {"USER": "alice", "HOME": "/home/alice"})
        self.assertEqual(out, "Hello alice at /home/alice/work")


# ════════════════════════════════════════════════════════════════════════════════
# 2.  testfabric.execution  — Local Executor, Runner Protocols
# ════════════════════════════════════════════════════════════════════════════════


class ExecutionLocalShowcase(unittest.TestCase):
    """Show running commands on the local host."""

    def test_local_executor_runs_echo(self) -> None:
        """Run a simple echo command and capture stdout."""
        from testfabric.execution import ExecutableCommand, LocalExecutorAdapter

        adapter = LocalExecutorAdapter()
        ctx = self._make_ctx()
        result = adapter.run(
            ctx,
            ExecutableCommand(cmd=["echo", "hello micro-packages"]),
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello micro-packages", result.stdout)

    def test_local_executor_captures_nonzero_exit(self) -> None:
        """Non-zero exit codes are captured, not raised."""
        from testfabric.execution import ExecutableCommand, LocalExecutorAdapter

        adapter = LocalExecutorAdapter()
        ctx = self._make_ctx()
        result = adapter.run(ctx, ExecutableCommand(cmd=["false"]))
        self.assertNotEqual(result.exit_code, 0)

    def test_local_executor_respects_timeout(self) -> None:
        """Jobs that exceed timeout are killed and flagged."""
        from testfabric.execution import ExecutableCommand, LocalExecutorAdapter

        adapter = LocalExecutorAdapter()
        ctx = self._make_ctx()
        result = adapter.run(
            ctx,
            ExecutableCommand(cmd=["sleep", "60"], timeout_seconds=1),
        )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)

    def _make_ctx(self) -> Any:
        with tempfile.TemporaryDirectory() as td:
            pass
        # Use a temporary dir that exists for the test
        td = tempfile.mkdtemp()
        return SimpleNamespace(
            repo_path=td,
            docker=None,
            executor="local",
            docker_endpoint={},
        )


class ExecutionRegistryShowcase(unittest.TestCase):
    """Show executor and runner registries."""

    def test_executor_registry_resolves_local(self) -> None:
        from testfabric.execution import ExecutorRegistry

        registry = ExecutorRegistry()
        adapter = registry.get("local")
        self.assertEqual(adapter.name, "local")

    def test_runner_registry_resolves_command(self) -> None:
        from testfabric.execution import RunnerRegistry

        registry = RunnerRegistry()
        runner = registry.get("command")
        self.assertEqual(runner.name, "command")

    def test_registry_rejects_unknown(self) -> None:
        from testfabric.execution import ExecutorRegistry

        registry = ExecutorRegistry()
        with self.assertRaises(ValueError):
            registry.get("kubernetes")


# ════════════════════════════════════════════════════════════════════════════════
# 3.  testfabric.workers  — Worker Pool & Target Management
# ════════════════════════════════════════════════════════════════════════════════


class WorkerPoolShowcase(unittest.TestCase):
    """Show pool-based worker allocation."""

    def test_allocate_workers_from_pool(self) -> None:
        """Allocate a subset of workers from a sized pool."""
        from testfabric.workers import WorkerPool

        pool = WorkerPool(mode="local", capacity=4)
        workers = pool.allocate(2)
        self.assertEqual(len(workers), 2)
        self.assertEqual(workers[0].id, "w000")
        self.assertEqual(workers[1].id, "w001")

    def test_acquire_and_release_exclusive_worker(self) -> None:
        """Acquire gives exclusive access; release returns to pool."""
        from testfabric.workers import WorkerPool

        pool = WorkerPool(mode="local", capacity=1)
        w = pool.acquire()
        self.assertIsNotNone(w)
        self.assertEqual(w.id, "w000")
        pool.release(w)
        # Can acquire again after release
        w2 = pool.acquire()
        self.assertEqual(w2.id, "w000")

    def test_unhealthy_workers_are_skipped(self) -> None:
        """Unhealthy workers are excluded from allocation."""
        from testfabric.workers import Worker, WorkerPool

        pool = WorkerPool(mode="local", capacity=3, health_state="unhealthy")
        allocated = pool.allocate(2)
        self.assertEqual(len(allocated), 0)


class TargetManagementShowcase(unittest.TestCase):
    """Show loading Ansible-style target inventories."""

    def test_load_flat_hosts_section(self) -> None:
        from testfabric.workers.targets import load_targets_mapping

        data = {
            "hosts": {
                "web1": {"address": "1.1.1.1", "labels": {"role": "web"}},
                "web2": {"address": "1.1.1.1"},
            }
        }
        targets = load_targets_mapping(data)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].host_id, "web1")
        self.assertEqual(targets[0].address, "1.1.1.1")

    def test_select_targets_by_group(self) -> None:
        from testfabric.workers.targets import load_targets_mapping, select_targets

        data = {
            "web": {"web1": {"address": "1.1.1.1"}, "web2": {"address": "1.1.1.1"}},
            "db": {"db1": {"address": "1.1.1.1"}},
        }
        targets = load_targets_mapping(data)
        selected = select_targets(targets, "group:web")
        self.assertEqual(len(selected), 2)

    def test_select_all_targets(self) -> None:
        from testfabric.workers.targets import load_targets_mapping, select_targets

        data = {"hosts": {"a": {"address": "1.1.1.1"}, "b": {"address": "2.2.2.2"}}}
        targets = load_targets_mapping(data)
        selected = select_targets(targets, "all")
        self.assertEqual(len(selected), 2)


# ════════════════════════════════════════════════════════════════════════════════
# 4.  testfabric.watch  — Reactive Watchers
# ════════════════════════════════════════════════════════════════════════════════


class WatchRuntimeShowcase(unittest.TestCase):
    """Show reactive watchers matching patterns in live output."""

    def _make_events(self) -> Any:
        @dataclass
        class Recorder:
            events: list = None
            def __post_init__(self):
                if self.events is None:
                    self.events = []
            def emit(self, event: Any) -> None:
                self.events.append(event)
            def stream(self, filename: str, text: str, echo_prefix: str | None = None) -> None:
                pass

        return Recorder()

    def test_match_pattern_triggers_report_action(self) -> None:
        """A watcher that matches 'ERROR' in stdout triggers a report."""
        from testfabric.watch import WatchRuntime

        events = self._make_events()
        watch = WatchRuntime(
            {
                "sources": [{"name": "out", "type": "session", "stream": "stdout"}],
                "watchers": [
                    {
                        "name": "error-detect",
                        "source": "out",
                        "match": "ERROR",
                        "action": "report",
                    },
                ],
            },
            events=events,
        )
        watch.feed_output("INFO all good\n", stream="stdout")
        self.assertEqual(len(watch.reports), 0)

        watch.feed_output("ERROR something broke\n", stream="stdout")
        self.assertEqual(len(watch.reports), 1)
        self.assertEqual(watch.reports[0]["watcher"], "error-detect")

    def test_fail_action_sets_abort_flag(self) -> None:
        """A watcher with action=fail stops execution."""
        from testfabric.watch import WatchRuntime

        events = self._make_events()
        watch = WatchRuntime(
            {
                "sources": [{"name": "out", "type": "session", "stream": "both"}],
                "watchers": [
                    {
                        "name": "fatal",
                        "source": "out",
                        "match": "FATAL",
                        "action": "fail",
                    },
                ],
            },
            events=events,
        )
        self.assertFalse(watch.abort_requested)
        watch.feed_output("FATAL crash\n", stream="stderr")
        self.assertTrue(watch.abort_requested)
        self.assertIn("fatal", watch.failure_reason)

    def test_sequence_watcher_detects_ordered_patterns(self) -> None:
        """Sequence watchers match patterns in order across multiple feeds."""
        from testfabric.watch import WatchRuntime

        events = self._make_events()
        watch = WatchRuntime(
            {
                "sources": [{"name": "log", "type": "session", "stream": "stdout"}],
                "watchers": [
                    {
                        "name": "boot-sequence",
                        "source": "log",
                        "when": {"sequence": ["INIT", "READY", "SERVING"]},
                        "then": {"report": {"message": "Boot complete"}},
                    },
                ],
            },
            events=events,
        )
        watch.feed_output("INIT starting\n", stream="stdout")
        self.assertEqual(len(watch.reports), 0)

        watch.feed_output("READY to serve\n", stream="stdout")
        self.assertEqual(len(watch.reports), 0)

        watch.feed_output("SERVING on :8080\n", stream="stdout")
        self.assertEqual(len(watch.reports), 1)
        self.assertIn("Boot complete", watch.reports[0].get("action_message", ""))


# ════════════════════════════════════════════════════════════════════════════════
# 5.  testfabric.spec  — YAML Spec Schema & Validation
# ════════════════════════════════════════════════════════════════════════════════


class SpecSchemaShowcase(unittest.TestCase):
    """Show loading and validating YAML run specifications."""

    def test_load_minimal_spec_from_yaml(self) -> None:
        """Parse a minimal spec with one command suite."""
        from testfabric.spec import RunSpec

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                """\
run:
  name: showcase
pipeline:
  stages:
    - suite: greet
      title: say hello
suites:
  greet:
    kind: command
    steps:
      - name: echo
        bash: echo hello
"""
            )
            f.flush()
            spec = RunSpec.load(f.name)
        self.assertEqual(spec.run.name, "showcase")
        self.assertEqual(len(spec.pipeline.stages), 1)
        self.assertIn("greet", spec.suites)

    def test_spec_rejects_unknown_suite_reference(self) -> None:
        """Spec cross-validation catches missing suite names."""
        from pydantic import ValidationError
        from testfabric.spec import RunSpec

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                """\
run:
  name: bad
pipeline:
  stages:
    - suite: nonexistent
suites:
  real:
    kind: command
    steps:
      - name: echo
        bash: echo hi
"""
            )
            f.flush()
            with self.assertRaises(ValidationError):
                RunSpec.load(f.name)

    def test_lint_detects_issues(self) -> None:
        """lint_raw_spec reports known issues like unimplemented workers.mode."""
        from testfabric.spec import lint_raw_spec

        raw = {
            "run": {"name": "test"},
            "workers": {"mode": "linode"},
            "pipeline": {"stages": []},
            "suites": {},
        }
        issues = lint_raw_spec(raw)
        self.assertTrue(len(issues) >= 1)
        self.assertIn("linode", issues[0].message)


# ════════════════════════════════════════════════════════════════════════════════
# 6.  testfabric.inputs  — Input Resolution & Profiles
# ════════════════════════════════════════════════════════════════════════════════


class InputsResolverShowcase(unittest.TestCase):
    """Show typed input resolution from defaults, env, CLI, and profiles."""

    def test_resolve_from_defaults(self) -> None:
        """Inputs with defaults resolve without external sources."""
        from testfabric.inputs import InputResolver, InputDefinition

        resolver = InputResolver()
        result = resolver.resolve(
            input_defs={
                "env": InputDefinition(type="string", default="dev"),
                "retries": InputDefinition(type="int", default=3),
            },
            env={},
        )
        self.assertEqual(result.values["env"], "dev")
        self.assertEqual(result.values["retries"], 3)
        self.assertEqual(result.sources["env"], "default")

    def test_cli_overrides_default(self) -> None:
        """CLI inputs take highest priority."""
        from testfabric.inputs import InputResolver, InputDefinition

        resolver = InputResolver()
        result = resolver.resolve(
            input_defs={
                "env": InputDefinition(type="string", default="dev"),
            },
            cli_inputs={"env": "prod"},
            env={},
        )
        self.assertEqual(result.values["env"], "prod")
        self.assertEqual(result.sources["env"], "cli")

    def test_missing_required_input_raises(self) -> None:
        """Required inputs without a source raise InputResolutionError."""
        from testfabric.inputs import InputResolver, InputDefinition, InputResolutionError

        resolver = InputResolver()
        with self.assertRaises(InputResolutionError):
            resolver.resolve(
                input_defs={"api_key": InputDefinition(type="secret", required=True)},
                env={},
            )


# ════════════════════════════════════════════════════════════════════════════════
# 7.  testfabric.artifacts  — Paths, Contracts & Collection
# ════════════════════════════════════════════════════════════════════════════════


class ArtifactPathsShowcase(unittest.TestCase):
    """Show deterministic run/stage directory layout."""

    def test_path_manager_creates_run_structure(self) -> None:
        """PathManager creates a predictable directory tree."""
        from testfabric.artifacts import PathManager, StageRef

        with tempfile.TemporaryDirectory() as td:
            spec = SimpleNamespace(
                run=SimpleNamespace(
                    artifacts_dir=str(Path(td) / "artifacts"),
                    runs_subdir="runs",
                    workers_tmp=str(Path(td) / "workers_tmp"),
                )
            )
            paths = PathManager(spec, "run-007")
            paths.ensure_run_dirs()

            self.assertTrue(paths.run_dir.exists())
            self.assertIn("run-007", str(paths.run_dir))
            self.assertTrue(paths.run_summary_path.parent.exists())

    def test_stage_ref_generates_slug(self) -> None:
        """StageRef generates a filesystem-safe slug."""
        from testfabric.artifacts import StageRef

        ref = StageRef(index=0, title="Deploy to Production", suite="deploy")
        self.assertIn("deploy", ref.slug)
        self.assertNotIn(" ", ref.slug)

    def test_artifact_contract_provides_env_vars(self) -> None:
        """ArtifactContract generates executor env vars."""
        from testfabric.artifacts import build_artifact_contract

        contract = build_artifact_contract(
            artifacts_root="/artifacts",
            run_id="r-01",
            stage_id="deploy",
            job_id="j-01",
            attempt=0,
        )
        env = contract.as_env()
        self.assertEqual(env["TESTFABRIC_RUN_ID"], "r-01")
        self.assertEqual(env["TESTFABRIC_STAGE_ID"], "deploy")
        self.assertIn("TESTFABRIC_ARTIFACTS_DIR", env)


# ════════════════════════════════════════════════════════════════════════════════
# 8.  testfabric.health  — Health Probes & Policy
# ════════════════════════════════════════════════════════════════════════════════


class HealthPolicyShowcase(unittest.TestCase):
    """Show health evaluation from probe results."""

    def test_all_passing_probes_yield_healthy(self) -> None:
        from testfabric.health import HealthCheckResult, evaluate_health

        checks = [
            HealthCheckResult(name="disk", ok=True, message="12 GB free"),
            HealthCheckResult(name="writable", ok=True, message="/tmp ok"),
        ]
        snapshot = evaluate_health(checks)
        self.assertEqual(snapshot.state, "healthy")
        self.assertEqual(len(snapshot.reasons), 0)

    def test_required_failure_yields_unhealthy(self) -> None:
        from testfabric.health import HealthCheckResult, evaluate_health

        checks = [
            HealthCheckResult(name="disk", ok=False, severity="required", message="Only 0.1 GB"),
        ]
        snapshot = evaluate_health(checks)
        self.assertEqual(snapshot.state, "unhealthy")
        self.assertIn("Only 0.1 GB", snapshot.reasons)

    def test_warning_failure_yields_degraded(self) -> None:
        from testfabric.health import HealthCheckResult, evaluate_health

        checks = [
            HealthCheckResult(name="disk", ok=True),
            HealthCheckResult(name="memory", ok=False, severity="warning", message="Low RAM"),
        ]
        snapshot = evaluate_health(checks)
        self.assertEqual(snapshot.state, "degraded")


# ════════════════════════════════════════════════════════════════════════════════
# 9.  testfabric.reporting  — Stage & Run Reporting
# ════════════════════════════════════════════════════════════════════════════════


class ReportingShowcase(unittest.TestCase):
    """Show stage/run summary generation."""

    def test_run_aggregator_writes_summary_json(self) -> None:
        """RunAggregator produces a structured summary.json."""
        from testfabric.orchestrator.aggregate import RunAggregator

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "artifacts" / "runs" / "r-01"
            run_dir.mkdir(parents=True)
            summary_path = run_dir / "summary.json"
            (run_dir / "events.jsonl").touch()

            paths = SimpleNamespace(
                run_dir=run_dir,
                run_summary_path=summary_path,
            )
            spec = SimpleNamespace(
                run=SimpleNamespace(name="showcase", repo_url=".", ref="HEAD")
            )
            agg = RunAggregator()
            agg.write_run_summary(
                spec=spec,
                paths=paths,
                run_id="r-01",
                ok=True,
                stage_results=[],
            )

            self.assertTrue(summary_path.exists())
            payload = json.loads(summary_path.read_text())
            self.assertEqual(payload["run"]["run_id"], "r-01")
            self.assertEqual(payload["run"]["verdict"], "PASSED")

    def test_run_aggregator_verdict_is_failed_when_not_ok(self) -> None:
        from testfabric.orchestrator.aggregate import RunAggregator

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run-bad"
            run_dir.mkdir(parents=True)
            (run_dir / "events.jsonl").touch()
            summary_path = run_dir / "summary.json"
            paths = SimpleNamespace(run_dir=run_dir, run_summary_path=summary_path)
            spec = SimpleNamespace(run=SimpleNamespace(name="bad", repo_url=".", ref="HEAD"))

            RunAggregator().write_run_summary(
                spec=spec, paths=paths, run_id="r-bad", ok=False,
                stage_results=[], error="timeout",
            )
            payload = json.loads(summary_path.read_text())
            self.assertEqual(payload["run"]["verdict"], "FAILED")
            self.assertEqual(payload["run"]["error"], "timeout")


# ════════════════════════════════════════════════════════════════════════════════
# 10. testfabric.evidence  — Structured Evidence Recording
# ════════════════════════════════════════════════════════════════════════════════


class EvidenceRecorderShowcase(unittest.TestCase):
    """Show evidence recording for library/programmatic use."""

    def test_record_step_events_and_json_attachment(self) -> None:
        from testfabric.evidence import EvidenceRecorder
        from testfabric.runtime import ArtifactLayout

        with tempfile.TemporaryDirectory() as td:
            layout = ArtifactLayout.for_run("r-ev-01", artifact_dir=td).ensure()
            recorder = EvidenceRecorder(layout)

            with recorder.step("deploy"):
                recorder.attach_json("config", {"env": "prod", "replicas": 3})

            events_text = layout.events_path.read_text()
            events = [json.loads(line) for line in events_text.strip().splitlines()]
            kinds = [e["kind"] for e in events]
            self.assertIn("step", kinds)
            self.assertIn("attachment", kinds)

    def test_record_command_result(self) -> None:
        from testfabric.evidence import EvidenceRecorder
        from testfabric.runtime import ArtifactLayout
        from testfabric.commands import CommandResult

        with tempfile.TemporaryDirectory() as td:
            layout = ArtifactLayout.for_run("r-ev-02", artifact_dir=td).ensure()
            recorder = EvidenceRecorder(layout)

            cmd_result = CommandResult(
                command=["curl", "-s", "http://localhost:8080/health"],
                exit_code=0,
                stdout='{"status":"ok"}',
                stderr="",
            )
            path = recorder.record_command("health-check", cmd_result)
            self.assertTrue(path.exists())
            self.assertIn("result.json", str(path))

    def test_write_summary(self) -> None:
        from testfabric.evidence import EvidenceRecorder
        from testfabric.runtime import ArtifactLayout

        with tempfile.TemporaryDirectory() as td:
            layout = ArtifactLayout.for_run("r-ev-03", artifact_dir=td).ensure()
            recorder = EvidenceRecorder(layout)

            path = recorder.write_summary({"verdict": "PASSED", "duration_ms": 1234})
            payload = json.loads(path.read_text())
            self.assertEqual(payload["verdict"], "PASSED")


# ════════════════════════════════════════════════════════════════════════════════
# 11. testfabric.infra  — LiveLogger
# ════════════════════════════════════════════════════════════════════════════════


class InfraLiveLoggerShowcase(unittest.TestCase):
    """Show the real-time event+stream logger."""

    def test_live_logger_writes_events_and_streams(self) -> None:
        from testfabric.infra import LiveLogger
        from testfabric.core import Event

        with tempfile.TemporaryDirectory() as td:
            logger = LiveLogger(Path(td), echo=False, run_id="r-ll")
            logger.emit(Event("stage", "start", "ok", {"index": 0}))
            logger.stream("logs/test.log", "line one\nline two\n")

            events = Path(td, "events.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(events), 1)
            self.assertEqual(json.loads(events[0])["component"], "stage")

            log_content = Path(td, "logs", "test.log").read_text()
            self.assertIn("line one", log_content)

    def test_live_logger_redacts_secrets(self) -> None:
        from testfabric.infra import LiveLogger

        with tempfile.TemporaryDirectory() as td:
            logger = LiveLogger(Path(td), echo=False, redaction_values=("XXXX",))
            logger.stream("out.log", "token=XXXX done")

            content = Path(td, "out.log").read_text()
            self.assertNotIn("XXXX", content)
            self.assertIn("***", content)


# ════════════════════════════════════════════════════════════════════════════════
# 12. testfabric.orchestrator  — Parallel Dispatch
# ════════════════════════════════════════════════════════════════════════════════


class ParallelDispatchShowcase(unittest.TestCase):
    """Show the threaded parallel dispatcher running jobs."""

    def _make_stage_ctx(self, root: Path) -> Any:
        from testfabric.artifacts import PathManager, StageRef
        from testfabric.core.context import RepoSnapshot, RunContext, StageContext
        from testfabric.core import NullEvents

        spec = SimpleNamespace(
            run=SimpleNamespace(
                artifacts_dir=str(root / "artifacts"),
                runs_subdir="runs",
                workers_tmp=str(root / "workers_tmp"),
            )
        )
        paths = PathManager(spec, "r-disp")
        paths.ensure_run_dirs()

        stage_ref = StageRef(index=0, title="parallel-demo", suite="test")
        run_ctx = RunContext(
            run_id="r-disp",
            mode="run",
            paths=paths,
            repo=RepoSnapshot(repo_path=str(root), commit_sha="abc123"),
            events=NullEvents(),
            redaction_values=(),
        )
        paths.ensure_worker_tmp_dirs("w000")
        paths.ensure_worker_stage_dirs("w000", stage_ref)

        return StageContext(
            run=run_ctx,
            suite_name="test",
            stage_index=0,
            stage_title="parallel-demo",
            stage_slug="s0-parallel-demo-test",
            stage_ref=stage_ref,
            executor="local",
            runner="command",
            kind="command",
            build=False,
            max_workers=2,
            chunk_size=1,
            max_retries=0,
            worker_id="w000",
        )

    def test_dispatch_runs_multiple_jobs_in_parallel(self) -> None:
        """LocalDispatcher runs jobs concurrently using a thread pool."""
        from testfabric.orchestrator import LocalDispatcher
        from testfabric.execution import ExecutableCommand, LocalExecutorAdapter, JobPlan, TestItem

        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_stage_ctx(Path(td))
            plan = [
                JobPlan(job_id="j1", items=[TestItem(id="t1")]),
                JobPlan(job_id="j2", items=[TestItem(id="t2")]),
                JobPlan(job_id="j3", items=[TestItem(id="t3")]),
            ]

            def make_cmd(job: JobPlan, attempt: int, reports_dir: str) -> ExecutableCommand:
                return ExecutableCommand(
                    cmd=["echo", f"job={job.job_id}"],
                    artifacts_dir=reports_dir,
                    artifacts_root=reports_dir,
                )

            dispatcher = LocalDispatcher()
            result = dispatcher.run_jobs(
                ctx=ctx,
                plan=plan,
                make_cmd=make_cmd,
                executor_adapter=LocalExecutorAdapter(),
            )
            self.assertEqual(len(result.results), 3)
            self.assertEqual(len(result.failed_job_ids), 0)

    def test_dispatch_captures_failures(self) -> None:
        """Failed jobs are tracked in failed_job_ids."""
        from testfabric.orchestrator import LocalDispatcher
        from testfabric.execution import ExecutableCommand, LocalExecutorAdapter, JobPlan, TestItem

        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_stage_ctx(Path(td))
            plan = [JobPlan(job_id="fail-j", items=[TestItem(id="t1")])]

            def make_cmd(job: JobPlan, attempt: int, reports_dir: str) -> ExecutableCommand:
                return ExecutableCommand(
                    cmd=["false"],
                    artifacts_dir=reports_dir,
                    artifacts_root=reports_dir,
                )

            result = LocalDispatcher().run_jobs(
                ctx=ctx, plan=plan, make_cmd=make_cmd,
                executor_adapter=LocalExecutorAdapter(),
            )
            self.assertEqual(len(result.failed_job_ids), 1)
            self.assertEqual(result.failed_job_ids[0], "fail-j")


# ════════════════════════════════════════════════════════════════════════════════
# 13. testfabric.runtime  — Library Run Context
# ════════════════════════════════════════════════════════════════════════════════


class RuntimeShowcase(unittest.TestCase):
    """Show the library-mode run context."""

    def test_run_context_from_request(self) -> None:
        from testfabric.runtime import RunContext, LibraryRunRequest

        with tempfile.TemporaryDirectory() as td:
            ctx = RunContext.from_request(
                LibraryRunRequest(
                    project="my-service",
                    suite="integration",
                    artifact_dir=td,
                    redaction_values=("XXXX",),
                )
            )
            self.assertEqual(ctx.project, "my-service")
            self.assertTrue(ctx.run_dir.exists())
            self.assertEqual(ctx.redact("token=XXXX"), "token=***")

    def test_artifact_layout_directory_structure(self) -> None:
        from testfabric.runtime import ArtifactLayout

        with tempfile.TemporaryDirectory() as td:
            layout = ArtifactLayout.for_run("run-42", artifact_dir=td).ensure()
            self.assertTrue(layout.run_dir.exists())
            self.assertTrue(layout.commands_dir.exists())
            self.assertTrue(layout.checks_dir.exists())
            self.assertTrue(layout.logs_dir.exists())
            self.assertTrue(layout.events_path.exists())


# ════════════════════════════════════════════════════════════════════════════════
# 14. testfabric.commands  — Command Result Model
# ════════════════════════════════════════════════════════════════════════════════


class CommandsShowcase(unittest.TestCase):
    """Show the command result model."""

    def test_command_result_ok_property(self) -> None:
        from testfabric.commands import CommandResult

        ok = CommandResult(command=["echo"], exit_code=0, stdout="hi\n")
        self.assertTrue(ok.ok)

        fail = CommandResult(command=["false"], exit_code=1)
        self.assertFalse(fail.ok)

    def test_command_result_to_json(self) -> None:
        from testfabric.commands import CommandResult

        result = CommandResult(command=["ls", "-la"], exit_code=0, stdout="total 8\n")
        data = json.loads(result.to_json())
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["command"], "ls -la")


if __name__ == "__main__":
    unittest.main()

