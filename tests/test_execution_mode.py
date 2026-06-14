from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from testfabric.execution.suites.base import TestItem
from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.orchestrator.plan.job_planner import Planner
from testfabric.spec.schema import RunSpec


class ExecutionModeTests(unittest.TestCase):
    def test_compile_run_plan_carries_repeat_execution(self) -> None:
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
                        - title: stress
                          suite: cmd
                          executor: local
                          max_workers: 3
                          execution:
                            mode: repeat
                            count: 20

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: run
                            cmd: ["bash", "-lc", "echo hi"]

                    parallelism:
                      max_workers: 5
                      chunk_size: 2
                      max_retries: 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertEqual(len(plan.stages), 1)
        stage = plan.stages[0]
        self.assertEqual(stage.execution_mode, "repeat")
        self.assertEqual(stage.execution_count, 20)
        self.assertEqual(stage.max_workers, 3)

    def test_planner_repeat_creates_one_job_per_repeated_item(self) -> None:
        items = [TestItem(id="a"), TestItem(id="b")]

        result = Planner().plan(items, chunk_size=25, execution_mode="repeat", execution_count=3)

        self.assertEqual(result.items_total, 6)
        self.assertEqual(result.chunk_size, 1)
        self.assertEqual(len(result.jobs), 6)
        self.assertTrue(all(len(job.items) == 1 for job in result.jobs))
        self.assertEqual([job.items[0].id for job in result.jobs], ["a", "b", "a", "b", "a", "b"])


if __name__ == "__main__":
    unittest.main()
