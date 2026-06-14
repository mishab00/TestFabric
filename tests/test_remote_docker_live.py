from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from git import Repo

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.spec.schema import RunSpec


def _make_commit(repo: Repo, repo_dir: Path, relpath: str, content: str, message: str) -> str:
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message).hexsha


class LiveRemoteDockerExecutionTests(unittest.TestCase):
    def _remote_bootstrap_paths(self) -> tuple[str, str, str, str]:
        host = os.environ.get("TESTFABRIC_REMOTE_DOCKER_HOST", "1.1.1.1").strip()
        user = os.environ.get("TESTFABRIC_REMOTE_DOCKER_USER", "root").strip()
        bootstrap_dir = Path(
            os.environ.get(
                "TESTFABRIC_REMOTE_DOCKER_BOOTSTRAP_DIR",
                "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root",
            )
        )
        key_path = Path(
            os.environ.get(
                "TESTFABRIC_REMOTE_DOCKER_KEY_PATH",
                str(bootstrap_dir / "id_ed25519"),
            )
        )
        known_hosts = Path(
            os.environ.get(
                "TESTFABRIC_REMOTE_DOCKER_KNOWN_HOSTS",
                str(bootstrap_dir / "known_hosts"),
            )
        )

        if not key_path.exists() or not known_hosts.exists():
            self.skipTest("Remote Docker bootstrap artifacts are not available")

        return host, user, str(key_path), str(known_hosts)

    def _remote_docker_reachable(self, host: str, user: str, key_path: str, known_hosts: str) -> bool:
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-i",
                    key_path,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"UserKnownHostsFile={known_hosts}",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    "ConnectTimeout=5",
                    f"{user}@{host}",
                    "true",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return False
        return result.returncode == 0

    def test_orchestrator_executes_command_on_remote_docker_endpoint(self) -> None:
        host, user, key_path, known_hosts = self._remote_bootstrap_paths()
        if not self._remote_docker_reachable(host, user, key_path, known_hosts):
            self.skipTest("Remote Docker endpoint is not reachable in this environment")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo = Repo.init(repo_dir)
            _make_commit(repo, repo_dir, "README.txt", "remote docker live\n", "init")
            ref = repo.active_branch.name

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: remote-docker-live
                      repo_url: "{repo_dir}"
                      ref: "{ref}"
                      workdir: "{root / 'work'}"
                      artifacts_dir: "{root / 'artifacts'}"

                    workers:
                      mode: local
                      max_workers: 1

                    targets:
                      builders:
                        builder-a:
                          address: "{host}"
                          credential: remote_docker

                    credentials:
                      remote_docker:
                        user: "{user}"
                        port: 22
                        key_path: "{key_path}"
                        known_hosts: "{known_hosts}"

                    pipeline:
                      stages:
                        - title: remote-docker-live
                          suite: smoke
                          executor: docker
                          targets: builders

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: emit
                            shell: hostname
                            stdout_to: hostname.txt

                    docker:
                      mode: template
                      image: testfabric-remote-live:ci
                      base_image: python:3.11-slim
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
            result = Orchestrator(spec, RunOptions.from_spec_and_cli(spec)).run()

            self.assertTrue(result["ok"], msg=result.get("error"))

            stage_dir = Path(result["run_dir"]) / "workers" / "w000" / "stages" / "01-remote-docker-live"
            self.assertTrue((stage_dir / "logs" / "docker-build.log").exists())
            self.assertTrue((stage_dir / "reports" / "artifacts" / "hostname.txt").exists())
            self.assertGreaterEqual(
                int((json.loads((stage_dir / "stage-summary.json").read_text(encoding="utf-8")).get("artifacts") or {}).get("files_total") or 0),
                1,
            )
            self.assertEqual(spec.pipeline.stages[0].targets, "builders")


if __name__ == "__main__":
    unittest.main()
