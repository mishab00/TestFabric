from __future__ import annotations

import os
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from git import Repo

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


def _commit_file(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


class PytestExecutionTests(unittest.TestCase):
    def test_orchestrator_executes_pytest_locally_and_collects_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)

            _commit_file(
                repo,
                repo_dir,
                "tests/test_sample.py",
                textwrap.dedent(
                    """
                    import os
                    from pathlib import Path

                    def test_generates_artifacts():
                        artifact_dir = Path(os.environ["TESTFABRIC_JOB_ARTIFACTS_DIR"])
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                        (artifact_dir / "note.txt").write_text("pytest-output\\n", encoding="utf-8")
                        assert os.environ["TESTFABRIC_RUN_ID"]
                    """
                ).strip()
                + "\n",
                "add tests",
            )
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: pytest-local
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: pytest-local
                          suite: tests
                          executor: local

                    suites:
                      tests:
                        kind: pytest
                        path: tests
                        args: "-q"

                    parallelism:
                      max_workers: 1
                      chunk_size: 10
                      max_retries: 0

                    outputs:
                      junit: junit.xml
                      html: null
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            opts = RunOptions.from_spec_and_cli(spec)
            result = Orchestrator(spec, opts).run()

            self.assertTrue(result["ok"], msg=result.get("error"))

            run_summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))
            stage_payload = run_summary["stages"][0]
            self.assertEqual(stage_payload["jobs_total"], 1)
            self.assertEqual(stage_payload["jobs_failed"], 0)

            artifact_payload = dict(stage_payload.get("artifacts") or {})
            self.assertIn("junit", artifact_payload["counts_by_category"])
            self.assertGreaterEqual(artifact_payload["counts_by_category"]["junit"], 1)
            self.assertGreaterEqual(artifact_payload["counts_by_category"]["artifact"], 1)
            self.assertTrue(any(item["category"] == "junit" for item in artifact_payload["highlights"]))
            self.assertTrue(any(item["label"] == "JUnit XML" for item in artifact_payload["highlights"]))

            primary_paths = list(artifact_payload.get("primary") or [])
            self.assertTrue(any(path.endswith("note.txt") for path in primary_paths))
            self.assertTrue(any(path.endswith(".xml") for path in primary_paths))

            worker_stage_dir = (
                Path(result["run_dir"])
                / "workers"
                / "w000"
                / "stages"
                / "01-pytest-local"
            )
            self.assertTrue((worker_stage_dir / "logs" / "collect-cmd.txt").exists())
            self.assertTrue((worker_stage_dir / "logs" / "collect-stdout.log").exists())
            self.assertTrue((worker_stage_dir / "reports" / "junit-job-0001-a0.xml").exists())

    def test_pytest_respects_custom_layout_and_runtime_env_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)

            _commit_file(
                repo,
                repo_dir,
                "tests/test_layout.py",
                textwrap.dedent(
                    """
                    import json
                    import os
                    from pathlib import Path

                    def test_runtime_contract():
                        artifact_dir = Path(os.environ["TESTFABRIC_JOB_ARTIFACTS_DIR"])
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                        payload = {
                            "TESTFABRIC_ARTIFACTS_DIR": os.environ["TESTFABRIC_ARTIFACTS_DIR"],
                            "TESTFABRIC_JOB_ARTIFACTS_DIR": os.environ["TESTFABRIC_JOB_ARTIFACTS_DIR"],
                            "TESTFABRIC_RUN_ID": os.environ["TESTFABRIC_RUN_ID"],
                            "TESTFABRIC_STAGE_ID": os.environ["TESTFABRIC_STAGE_ID"],
                            "TESTFABRIC_JOB_ID": os.environ["TESTFABRIC_JOB_ID"],
                            "TESTFABRIC_ATTEMPT": os.environ["TESTFABRIC_ATTEMPT"],
                            "TOKEN": os.environ["TOKEN"],
                            "PASSWORD": os.environ["PASSWORD"],
                        }
                        (artifact_dir / "layout.json").write_text(
                            json.dumps(payload, indent=2, sort_keys=True),
                            encoding="utf-8",
                        )
                        assert payload["TOKEN"] == "XXXX"
                        assert payload["PASSWORD"] == "XXXX"
                    """
                ).strip()
                + "\n",
                "add layout test",
            )
            ref = repo.active_branch.name

            artifacts_root = root / "custom-artifacts-root"
            workers_tmp = root / "custom-workers-tmp"
            run_id = "pytest-layout-fixed"

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    inputs:
                      token:
                        required: true
                        env: TOKEN
                      password:
                        type: secret
                        required: true
                        env: PASSWORD

                    run:
                      name: pytest-layout
                      run_id: "{run_id}"
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{artifacts_root}"
                      workers_tmp: "{workers_tmp}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: pytest-layout
                          suite: tests
                          executor: local

                    suites:
                      tests:
                        kind: pytest
                        path: tests
                        args: "-q"
                        extra_env:
                          TOKEN: "${{inputs.token}}"
                          PASSWORD: "${{inputs.password}}"

                    parallelism:
                      max_workers: 1
                      chunk_size: 10
                      max_retries: 0

                    outputs:
                      junit: custom-junit.xml
                      html: null
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {"TOKEN": "XXXX", "PASSWORD": "XXXX"},
                clear=False,
            ):
                spec, resolved_inputs = RunSpec.load_resolved(str(spec_path))
                opts = RunOptions.from_spec_and_cli(spec, resolved_inputs=resolved_inputs.values)
                result = Orchestrator(spec, opts).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            self.assertEqual(Path(result["run_dir"]), (artifacts_root / "runs" / run_id).resolve())

            worker_stage_tmp_dir = workers_tmp / run_id / "w000" / "stages" / "01-pytest-layout"
            worker_stage_mirror_dir = artifacts_root / "runs" / run_id / "workers" / "w000" / "stages" / "01-pytest-layout"

            self.assertTrue(worker_stage_tmp_dir.exists())
            self.assertTrue(worker_stage_mirror_dir.exists())
            self.assertTrue((worker_stage_mirror_dir / "logs").exists())
            self.assertTrue((worker_stage_mirror_dir / "jobs").exists())
            self.assertTrue((worker_stage_mirror_dir / "reports").exists())
            self.assertTrue((worker_stage_mirror_dir / "stage-summary.json").exists())
            self.assertTrue((worker_stage_tmp_dir / "reports" / "custom-junit-job-0001-a0.xml").exists())

            layout_payload = json.loads(
                (
                    worker_stage_tmp_dir
                    / "reports"
                    / "artifacts"
                    / "jobs"
                    / "job-0001"
                    / "attempt-0"
                    / "layout.json"
                ).read_text(encoding="utf-8")
            )

            expected_artifacts_dir = str((worker_stage_tmp_dir / "reports").resolve())
            expected_job_artifacts_dir = str(
                (
                    worker_stage_tmp_dir
                    / "reports"
                    / "artifacts"
                    / "jobs"
                    / "job-0001"
                    / "attempt-0"
                ).resolve()
            )

            self.assertEqual(layout_payload["TESTFABRIC_ARTIFACTS_DIR"], expected_artifacts_dir)
            self.assertEqual(layout_payload["TESTFABRIC_JOB_ARTIFACTS_DIR"], expected_job_artifacts_dir)
            self.assertEqual(layout_payload["TESTFABRIC_RUN_ID"], run_id)
            self.assertEqual(layout_payload["TESTFABRIC_STAGE_ID"], "01-pytest-layout")
            self.assertEqual(layout_payload["TESTFABRIC_JOB_ID"], "job-0001")
            self.assertEqual(layout_payload["TESTFABRIC_ATTEMPT"], "0")
            self.assertEqual(layout_payload["TOKEN"], "XXXX")
            self.assertEqual(layout_payload["PASSWORD"], "XXXX")

            stage_summary = json.loads(
                Path(result["stages"][0]["stage_summary_path"]).read_text(encoding="utf-8")
            )
            self.assertNotIn("stage_reports_dir", stage_summary["debug"])


def test_portal_retry_probe() -> None:
    attempt = os.environ.get("TESTFABRIC_ATTEMPT")
    artifact_dir = os.environ.get("TESTFABRIC_JOB_ARTIFACTS_DIR")
    if attempt is None or artifact_dir is None:
        raise unittest.SkipTest("retry probe only runs inside a TestFabric job environment")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "attempt.txt").write_text(f"attempt={attempt}\n", encoding="utf-8")
    assert attempt != "0"


if __name__ == "__main__":
    unittest.main()
