from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from git import Repo

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


def _commit_file(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


class ReportingFailureTests(unittest.TestCase):
    def test_command_failure_is_classified_as_execution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)
            _commit_file(repo, repo_dir, "README.txt", "sample\n", "init")
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: report-failure-command
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: command-fail
                          suite: cmd
                          executor: local

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: fail
                            cmd: ["bash", "-lc", "exit 1"]

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            result = Orchestrator(spec, RunOptions.from_spec_and_cli(spec)).run()
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        stage = summary["stages"][0]
        job = stage["jobs"][0]
        self.assertEqual(stage["failure_type"], "execution")
        self.assertEqual(job["failure_type"], "execution")
        self.assertEqual(summary["totals"]["jobs_by_failure_type"]["execution"], 1)
        self.assertEqual(summary["totals"]["stages_by_failure_type"]["execution"], 1)
        self.assertEqual(summary["failures"][0]["failure_type"], "execution")
        self.assertEqual(summary["failures"][0]["executor"], "local")
        self.assertIn("headline", summary["run"]["failure"])
        self.assertIn("command-fail", summary["run"]["failure"]["headline"])
        self.assertEqual(summary["run"]["failure"]["first_failed_job"], "job-0001")
        self.assertTrue(summary["run"]["failure"]["root_cause"])

    def test_missing_command_is_classified_as_infra(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)
            _commit_file(repo, repo_dir, "README.txt", "sample\n", "init")
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: report-failure-infra
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: missing-command
                          suite: cmd
                          executor: local

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: fail
                            cmd: ["definitely-missing-command-binary"]

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            result = Orchestrator(spec, RunOptions.from_spec_and_cli(spec)).run()
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        stage = summary["stages"][0]
        job = stage["jobs"][0]
        self.assertEqual(stage["failure_type"], "infra")
        self.assertEqual(job["failure_type"], "infra")
        self.assertEqual(summary["totals"]["jobs_by_failure_type"]["infra"], 1)
        self.assertEqual(summary["totals"]["stages_by_failure_type"]["infra"], 1)
        self.assertEqual(summary["failures"][0]["failure_type"], "infra")
        self.assertTrue(summary["failures"][0]["job_started"])
        self.assertIn("headline", summary["run"]["failure"])
        self.assertIn("command not found", summary["run"]["failure"]["headline"])
        self.assertEqual(summary["run"]["failure"]["first_failed_job"], "job-0001")
        self.assertTrue(summary["run"]["failure"]["root_cause"])

    def test_pytest_failure_is_classified_as_runner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)
            _commit_file(repo, repo_dir, "tests/test_sample.py", "def test_fail():\n    assert False\n", "add test")
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: report-failure-pytest
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: pytest-fail
                          suite: smoke
                          executor: local

                    suites:
                      smoke:
                        kind: pytest
                        path: tests

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            result = Orchestrator(spec, RunOptions.from_spec_and_cli(spec)).run()
            summary = json.loads(Path(result["run_summary_path"]).read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        stage = summary["stages"][0]
        job = stage["jobs"][0]
        self.assertEqual(stage["failure_type"], "runner")
        self.assertEqual(job["failure_type"], "runner")
        self.assertEqual(summary["totals"]["jobs_by_failure_type"]["runner"], 1)
        self.assertEqual(summary["totals"]["stages_by_failure_type"]["runner"], 1)
        self.assertEqual(summary["failures"][0]["failure_type"], "runner")
        self.assertIn("headline", summary["run"]["failure"])
        self.assertIn("pytest-fail", summary["run"]["failure"]["headline"])
        self.assertEqual(summary["run"]["failure"]["first_failed_job"], "job-0001")
        self.assertTrue(summary["run"]["failure"]["root_cause"])


if __name__ == "__main__":
    unittest.main()
