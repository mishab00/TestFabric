from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from testfabric.core.events import Event
from testfabric.infra.live_logger import LiveLogger


class ParallelLoggingTests(unittest.TestCase):
    def test_live_logger_writes_valid_jsonl_under_parallel_load(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = LiveLogger(run_dir=root, echo=False)

            def worker(worker_id: int) -> None:
                for idx in range(25):
                    logger.emit(
                        Event(
                            "job",
                            "tick",
                            "success",
                            {"worker": worker_id, "idx": idx},
                        )
                    )
                    logger.stream("logs/stdout.stream.log", f"worker={worker_id} idx={idx}\n")

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(worker, wid) for wid in range(8)]
                for fut in futures:
                    fut.result()

            events_path = root / "events.jsonl"
            lines = events_path.read_text(encoding="utf-8").splitlines()

            self.assertEqual(len(lines), 200)
            parsed = [json.loads(line) for line in lines]
            self.assertTrue(all(item["component"] == "job" for item in parsed))
            self.assertTrue(all(item["action"] == "tick" for item in parsed))
            self.assertEqual([item["seq"] for item in parsed], list(range(1, 201)))
            self.assertTrue(all("message" in item for item in parsed))
            self.assertTrue(all(item["run_id"] is None for item in parsed))

            stream_lines = (root / "logs" / "stdout.stream.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(stream_lines), 200)

    def test_live_logger_sanitizes_outside_stream_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logger = LiveLogger(run_dir=root, echo=False)

            logger.stream("../../escape.log", "hello\n")

            self.assertFalse((root.parent / "escape.log").exists())
            self.assertEqual((root / "logs" / "stream.log").read_text(encoding="utf-8"), "hello\n")


if __name__ == "__main__":
    unittest.main()
