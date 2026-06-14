from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from testfabric.core.events import Event
from testfabric.infra.live_logger import LiveLogger
from testfabric.orchestrator.orchestrator import RunOptions


class CliVerbosityTests(unittest.TestCase):
    def test_run_options_use_spec_default_and_cli_override(self) -> None:
        spec = SimpleNamespace(run=SimpleNamespace(name="sample", run_id=None, console_verbosity="summary"))

        opts = RunOptions.from_spec_and_cli(spec)
        self.assertEqual(opts.console_verbosity, "summary")

        overridden = RunOptions.from_spec_and_cli(spec, console_verbosity="verbose")
        self.assertEqual(overridden.console_verbosity, "verbose")

    def test_summary_verbosity_suppresses_job_noise_and_stream_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            stream = io.StringIO()
            with redirect_stdout(stream):
                logger = LiveLogger(Path(td), console_verbosity="summary")
                logger.emit(Event("run", "start", "start", {"run_id": "run-1"}))
                logger.emit(Event("job", "start", "start", {"job_id": "job-1"}))
                logger.emit(Event("job", "end", "success", {"job_id": "job-1"}))
                logger.emit(Event("job", "end", "fail", {"job_id": "job-2", "error": "boom"}))
                logger.stream("logs/stdout.stream.log", "hello\n", echo_prefix="[job-1] ")

            output = stream.getvalue()
            self.assertIn("run:start start", output)
            self.assertIn("job:end fail", output)
            self.assertNotIn("job:start start", output)
            self.assertNotIn("[job-1] hello", output)

    def test_normal_verbosity_keeps_events_but_suppresses_stream_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            stream = io.StringIO()
            with redirect_stdout(stream):
                logger = LiveLogger(Path(td), console_verbosity="normal")
                logger.emit(Event("job", "start", "start", {"job_id": "job-1"}))
                logger.emit(Event("job", "end", "success", {"job_id": "job-1"}))
                logger.stream("logs/stdout.stream.log", "hello\n", echo_prefix="[job-1] ")

            output = stream.getvalue()
            self.assertIn("job:start start", output)
            self.assertIn("job:end success", output)
            self.assertNotIn("[job-1] hello", output)

    def test_verbose_verbosity_echoes_stream_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            stream = io.StringIO()
            with redirect_stdout(stream):
                logger = LiveLogger(Path(td), console_verbosity="verbose")
                logger.stream("logs/stdout.stream.log", "hello\n", echo_prefix="[job-1] ")

            self.assertIn("[job-1] hello", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
