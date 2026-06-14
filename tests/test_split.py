from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from testfabric.execution.suites.base import TestItem
from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.orchestrator.plan.job_planner import Planner
from testfabric.spec.schema import RunSpec


class SplitTests(unittest.TestCase):
    def test_split_rejects_legacy_strategy_field(self) -> None:
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
                      max_workers: 5

                    pipeline:
                      stages:
                        - title: split-me
                          suite: cmd
                          executor: local
                          split:
                            strategy: manifest
                            manifest_path: manifests/split.json

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: one
                            cmd: ["bash", "-lc", "echo one"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 10
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                RunSpec.load(str(spec_path))

    def test_split_rejects_legacy_manifest_env_field(self) -> None:
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
                      max_workers: 5

                    pipeline:
                      stages:
                        - title: split-me
                          suite: cmd
                          executor: local
                          split:
                            manifest_env: TESTFABRIC_SPLIT

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: one
                            cmd: ["bash", "-lc", "echo one"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 10
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                RunSpec.load(str(spec_path))

    def test_compile_run_plan_carries_split(self) -> None:
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
                      max_workers: 5

                    pipeline:
                      stages:
                        - title: split-me
                          suite: cmd
                          executor: local
                          split:
                            count: 3

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: one
                            cmd: ["bash", "-lc", "echo one"]
                          - name: two
                            cmd: ["bash", "-lc", "echo two"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 10
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertEqual(len(plan.stages), 1)
        stage = plan.stages[0]
        self.assertEqual(stage.split_count, 3)
        self.assertIsNone(stage.split_manifest_path)

    def test_compile_run_plan_carries_manifest_split(self) -> None:
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
                      max_workers: 5

                    pipeline:
                      stages:
                        - title: split-me
                          suite: cmd
                          executor: local
                          split:
                            manifest_path: manifests/split.json

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: one
                            cmd: ["bash", "-lc", "echo one"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 10
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        stage = plan.stages[0]
        self.assertEqual(stage.split_count, 1)
        self.assertEqual(stage.split_manifest_path, "manifests/split.json")

    def test_planner_split_creates_balanced_jobs(self) -> None:
        items = [TestItem(id=f"item-{i}") for i in range(1, 6)]

        result = Planner().plan(
            items,
            chunk_size=25,
            execution_mode="once",
            execution_count=1,
            split_count=3,
        )

        self.assertEqual(result.items_total, 5)
        self.assertEqual(len(result.jobs), 3)
        self.assertEqual(sorted(len(job.items) for job in result.jobs), [1, 2, 2])
        flattened = [item.id for job in result.jobs for item in job.items]
        self.assertEqual(sorted(flattened), sorted(item.id for item in items))

    def test_compile_allows_repeat_with_split(self) -> None:
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
                      max_workers: 5

                    pipeline:
                      stages:
                        - title: split-repeat
                          suite: cmd
                          executor: local
                          execution:
                            mode: repeat
                            count: 5
                          split:
                            count: 2

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: one
                            cmd: ["bash", "-lc", "echo one"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertEqual(plan.stages[0].execution_mode, "repeat")
        self.assertEqual(plan.stages[0].execution_count, 5)
        self.assertEqual(plan.stages[0].split_count, 2)

    def test_planner_repeat_with_split_repeats_groups(self) -> None:
        items = [TestItem(id=f"item-{i}") for i in range(1, 5)]

        result = Planner().plan(
            items,
            chunk_size=25,
            execution_mode="repeat",
            execution_count=3,
            split_count=2,
        )

        self.assertEqual(result.items_total, 12)
        self.assertEqual(len(result.jobs), 6)
        self.assertTrue(all(len(job.items) == 2 for job in result.jobs))
        expected_groups = [
            ["item-1", "item-3"],
            ["item-2", "item-4"],
            ["item-1", "item-3"],
            ["item-2", "item-4"],
            ["item-1", "item-3"],
            ["item-2", "item-4"],
        ]
        self.assertEqual([[item.id for item in job.items] for job in result.jobs], expected_groups)

    def test_planner_manifest_split_uses_manifest_groups(self) -> None:
        items = [TestItem(id=f"item-{i}") for i in range(1, 5)]

        result = Planner().plan(
            items,
            chunk_size=25,
            execution_mode="once",
            execution_count=1,
            split_count=1,
            split_manifest_path="manifests/split.json",
            split_manifest_groups=[["item-2", "item-4"], ["item-1", "item-3"]],
        )

        self.assertEqual(result.items_total, 4)
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(
            [[item.id for item in job.items] for job in result.jobs],
            [["item-2", "item-4"], ["item-1", "item-3"]],
        )

    def test_planner_split_uses_item_weights_when_available(self) -> None:
        items = [TestItem(id=f"item-{i}") for i in range(1, 5)]

        result = Planner().plan(
            items,
            chunk_size=25,
            execution_mode="once",
            execution_count=1,
            split_count=2,
            item_weights={
                "item-1": 9.0,
                "item-2": 8.0,
                "item-3": 1.0,
                "item-4": 1.0,
            },
        )

        self.assertEqual(
            [[item.id for item in job.items] for job in result.jobs],
            [["item-1", "item-4"], ["item-2", "item-3"]],
        )


if __name__ == "__main__":
    unittest.main()
