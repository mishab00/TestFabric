from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import textwrap
import unittest

from git import Repo

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import RepoSnapshot, RunContext, StageContext
from testfabric.orchestrator.plan.manifest import load_manifest_groups
from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


def _commit_file(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


class SplitManifestTests(unittest.TestCase):
    def test_checked_in_manifest_matches_example_spec(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest_path = repo_root / "manifests" / "split.json"
        spec_path = repo_root / "run_split_manifest_local.yaml"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = RunSpec.load(str(spec_path))

        groups = manifest.get("groups")
        self.assertIsInstance(groups, list)
        self.assertEqual(len(groups), 2)

        suite = spec.suites["split_cmd"]
        step_names = [str(step.name) for step in suite.steps]
        manifest_ids = [str(item) for group in groups for item in group]

        self.assertEqual(sorted(manifest_ids), sorted(step_names))
        self.assertEqual(len(manifest_ids), len(set(manifest_ids)))

    def test_manifest_rejects_legacy_alias_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = repo_dir / "bad_manifest.json"
            manifest_path.write_text(json.dumps({"jobs": [["a"], ["b"]]}), encoding="utf-8")

            spec = SimpleNamespace(
                run=SimpleNamespace(
                    artifacts_dir=str(root / "artifacts"),
                    runs_subdir="runs",
                    workers_tmp=str(root / "workers_tmp"),
                )
            )
            paths = PathManager(spec, "run-001")
            run_ctx = RunContext(
                run_id="run-001",
                mode="run",
                paths=paths,
                repo=RepoSnapshot(repo_path=str(repo_dir), commit_sha="abc123"),
            )
            stage_ref = StageRef(index=1, title="Split", suite="cmd")
            ctx = StageContext(
                run=run_ctx,
                suite_name="cmd",
                stage_index=1,
                stage_title="Split",
                stage_slug=stage_ref.slug,
                stage_ref=stage_ref,
                executor="local",
                runner="command",
                kind="command",
                build=False,
                max_workers=1,
                chunk_size=1,
                max_retries=0,
                worker_id="w001",
                split_manifest_path=str(manifest_path),
            )

            with self.assertRaises(ValueError) as exc:
                load_manifest_groups(ctx, manifest_path=str(manifest_path))

        self.assertIn("top-level 'groups' field", str(exc.exception))

    def test_orchestrator_executes_manifest_defined_groups(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)
            _commit_file(repo, repo_dir, "README.txt", "sample\n", "init")
            manifest_path = repo_dir / "manifests" / "split.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps({"groups": [["step-2", "step-4"], ["step-1", "step-3"]]}, indent=2),
                encoding="utf-8",
            )
            repo.index.add(["manifests/split.json"])
            repo.index.commit("add manifest")
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: split-manifest
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 2

                    pipeline:
                      stages:
                        - title: split-by-manifest
                          suite: cmd
                          executor: local
                          split:
                            manifest_path: manifests/split.json

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: step-1
                            cmd: ["bash", "-lc", "echo step-1"]
                          - name: step-2
                            cmd: ["bash", "-lc", "echo step-2"]
                          - name: step-3
                            cmd: ["bash", "-lc", "echo step-3"]
                          - name: step-4
                            cmd: ["bash", "-lc", "echo step-4"]

                    parallelism:
                      max_workers: 2
                      chunk_size: 10
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            opts = RunOptions.from_spec_and_cli(spec)
            result = Orchestrator(spec, opts).run()

            self.assertTrue(result["stages"][0]["ok"])
            summary_path = Path(result["stages"][0]["stage_summary_path"])
            payload = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(payload["jobs_total"], 2)
        self.assertEqual(payload["items_total"], 4)
        self.assertEqual(payload["split"]["manifest_path"], "manifests/split.json")


if __name__ == "__main__":
    unittest.main()
