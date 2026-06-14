from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound
from git import Repo

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


def _commit_file(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


def _docker_client_or_none() -> docker.DockerClient | None:
    try:
        client = docker.from_env()
        client.ping()
        return client
    except DockerException:
        return None


class LiveDockerExecutionTests(unittest.TestCase):
    def test_orchestrator_executes_command_in_managed_docker(self) -> None:
        client = _docker_client_or_none()
        if client is None:
            self.skipTest("Docker daemon is not available")

        base_image = os.environ.get("TESTFABRIC_DOCKER_BASE_IMAGE", "python:3.11-slim").strip()
        try:
            client.images.get(base_image)
        except ImageNotFound:
            self.skipTest(
                f"Base image '{base_image}' is not available locally; "
                "set TESTFABRIC_DOCKER_BASE_IMAGE to a cached image to enable this test"
            )
        if shutil.which("docker-credential-desktop") is None:
            self.skipTest("docker-credential-desktop is not available in PATH")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)
            _commit_file(repo, repo_dir, "README.txt", "live docker\n", "init")
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: docker-live
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: docker-live
                          suite: cmd
                          executor: docker

                    suites:
                      cmd:
                        kind: command
                        steps:
                          - name: emit
                            cmd:
                              - sh
                              - -lc
                              - |
                                mkdir -p "$TESTFABRIC_JOB_ARTIFACTS_DIR"
                                printf "inside-docker\\n" > "$TESTFABRIC_ARTIFACTS_DIR/container.txt"
                                printf "%s\\n" "$TESTFABRIC_RUN_ID" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/run-id.txt"

                    docker:
                      mode: template
                      image: testfabric-live-integration:ci
                      base_image: "{base_image}"
                      repo_mount: /work
                      workdir: /work

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

            self.assertTrue(result["ok"], msg=result.get("error"))

            stage_dir = Path(result["run_dir"]) / "workers" / "w000" / "stages" / "01-docker-live"
            self.assertTrue((stage_dir / "logs" / "docker-build.log").exists())
            self.assertTrue((stage_dir / "reports" / "container.txt").exists())
            self.assertEqual(
                (stage_dir / "reports" / "container.txt").read_text(encoding="utf-8"),
                "inside-docker\n",
            )
            self.assertTrue(
                (stage_dir / "reports" / "artifacts" / "jobs" / "job-0001" / "attempt-0" / "run-id.txt").exists()
            )

            summary = json.loads((stage_dir / "stage-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["executor"], "docker")
            self.assertEqual(summary["jobs_total"], 1)
            self.assertEqual(summary["jobs_failed"], 0)


if __name__ == "__main__":
    unittest.main()
