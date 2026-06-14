from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from git import Repo

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.spec.schema import RunSpec


def _commit_file(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


class LifecycleTests(unittest.TestCase):
    def test_compile_run_plan_carries_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      name: sample
                      repo_url: git@example.com:org/repo.git
                      ref: main
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    workers:
                      mode: local
                      max_workers: 2

                    pipeline:
                      stages:
                        - title: cleanup
                          suite: cleanup
                          executor: local
                          lifecycle:
                            mode: teardown
                            when: always

                    suites:
                      cleanup:
                        kind: command
                        steps:
                          - name: done
                            cmd: ["bash", "-lc", "echo cleanup"]

                    parallelism:
                      max_workers: 2
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        stage = plan.stages[0]
        self.assertEqual(stage.lifecycle_mode, "teardown")
        self.assertEqual(stage.lifecycle_when, "always")

    def test_teardown_stage_runs_after_failure_when_always(self) -> None:
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
                      name: lifecycle
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: fail
                          suite: fail_cmd
                          executor: local
                        - title: cleanup
                          suite: cleanup_cmd
                          executor: local
                          lifecycle:
                            mode: teardown
                            when: always

                    suites:
                      fail_cmd:
                        kind: command
                        steps:
                          - name: fail
                            cmd: ["bash", "-lc", "exit 1"]

                      cleanup_cmd:
                        kind: command
                        steps:
                          - name: cleanup
                            cmd:
                              - bash
                              - -lc
                              - |
                                echo cleanup > teardown.txt

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
            opts = RunOptions.from_spec_and_cli(spec)
            result = Orchestrator(spec, opts).run()

            run_dir = Path(result["run_dir"])
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["stages"]), 2)
        self.assertEqual(result["stages"][1]["stage_title"], "cleanup")
        self.assertTrue(result["stages"][1]["ok"])

    def test_failed_setup_skips_run_stage_and_records_skip(self) -> None:
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
                      name: lifecycle
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: setup
                          suite: setup_cmd
                          executor: local
                          lifecycle:
                            mode: setup
                        - title: run
                          suite: run_cmd
                          executor: local
                        - title: cleanup
                          suite: cleanup_cmd
                          executor: local
                          lifecycle:
                            mode: teardown
                            when: always

                    suites:
                      setup_cmd:
                        kind: command
                        steps:
                          - name: fail-setup
                            cmd: ["bash", "-lc", "exit 1"]

                      run_cmd:
                        kind: command
                        steps:
                          - name: should-not-run
                            cmd: ["bash", "-lc", "echo run > run.txt"]

                      cleanup_cmd:
                        kind: command
                        steps:
                          - name: cleanup
                            cmd: ["bash", "-lc", "echo cleanup > cleanup.txt"]

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
            opts = RunOptions.from_spec_and_cli(spec)
            result = Orchestrator(spec, opts).run()

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["stages"]), 3)
        skipped = result["stages"][1]
        self.assertEqual(skipped["stage_title"], "run")
        self.assertTrue(skipped["skipped"])
        self.assertEqual(skipped["failure_type"], "skipped")
        self.assertEqual(skipped["skip_reason"], "prior lifecycle group failed")
        self.assertTrue(result["stages"][2]["ok"])

    def test_new_setup_group_runs_after_previous_group_failure_and_teardown(self) -> None:
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
                      name: lifecycle
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: setup-1
                          suite: setup_ok
                          executor: local
                          lifecycle:
                            mode: setup
                        - title: fail-1
                          suite: fail_cmd
                          executor: local
                        - title: cleanup-1
                          suite: cleanup_cmd
                          executor: local
                          lifecycle:
                            mode: teardown
                            when: always
                        - title: setup-2
                          suite: setup_ok
                          executor: local
                          lifecycle:
                            mode: setup
                        - title: run-2
                          suite: run_ok
                          executor: local

                    suites:
                      setup_ok:
                        kind: command
                        steps:
                          - name: setup
                            cmd: ["bash", "-lc", "echo setup"]

                      fail_cmd:
                        kind: command
                        steps:
                          - name: fail
                            cmd: ["bash", "-lc", "exit 1"]

                      cleanup_cmd:
                        kind: command
                        steps:
                          - name: cleanup
                            cmd: ["bash", "-lc", "echo cleanup"]

                      run_ok:
                        kind: command
                        steps:
                          - name: run
                            cmd: ["bash", "-lc", "echo run"]

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
            opts = RunOptions.from_spec_and_cli(spec)
            result = Orchestrator(spec, opts).run()

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["stages"]), 5)
        self.assertEqual(result["stages"][3]["stage_title"], "setup-2")
        self.assertTrue(result["stages"][3]["ok"])
        self.assertEqual(result["stages"][4]["stage_title"], "run-2")
        self.assertTrue(result["stages"][4]["ok"])


if __name__ == "__main__":
    unittest.main()
