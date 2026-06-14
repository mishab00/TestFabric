from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from testfabric.artifacts.paths import PathManager, StageRef


class ArtifactPathTests(unittest.TestCase):
    def test_workers_tmp_defaults_under_artifacts_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = SimpleNamespace(
                run=SimpleNamespace(
                    artifacts_dir=str(root / "artifact-root"),
                    runs_subdir="runs",
                    workers_tmp=None,
                )
            )

            paths = PathManager(spec, "run-001")

            self.assertEqual(paths.artifacts_root, (root / "artifact-root").resolve())
            self.assertEqual(paths.workers_tmp_root, (root / "artifact-root" / "workers_tmp").resolve())
            self.assertEqual(paths.run_summary_path, (root / "artifact-root" / "runs" / "run-001" / "summary.json").resolve())

    def test_run_and_worker_tmp_dirs_do_not_create_unused_legacy_folders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec = SimpleNamespace(
                run=SimpleNamespace(
                    artifacts_dir=str(root / "artifact-root"),
                    runs_subdir="runs",
                    workers_tmp=None,
                )
            )

            paths = PathManager(spec, "run-001")
            stage_ref = StageRef(index=1, title="Smoke", suite="smoke")

            paths.ensure_run_dirs()
            paths.ensure_worker_tmp_dirs("w001")
            paths.ensure_worker_stage_dirs("w001", stage_ref)

            self.assertTrue(paths.run_dir.exists())
            self.assertTrue(paths.run_logs_dir.exists())
            self.assertTrue(paths.run_workers_dir.exists())
            self.assertFalse((paths.run_dir / "stages").exists())

            worker_tmp_dir = paths.worker_tmp_dir("w001")
            self.assertTrue(worker_tmp_dir.exists())
            self.assertFalse((worker_tmp_dir / "logs").exists())
            self.assertFalse((worker_tmp_dir / "reports").exists())
            self.assertFalse((worker_tmp_dir / "outputs").exists())

            self.assertTrue(paths.worker_stage_logs_dir("w001", stage_ref).exists())
            self.assertTrue(paths.worker_stage_jobs_dir("w001", stage_ref).exists())
            self.assertTrue(paths.worker_stage_reports_dir("w001", stage_ref).exists())
            self.assertTrue(paths.worker_stage_output_dir("w001", stage_ref).exists())


if __name__ == "__main__":
    unittest.main()
