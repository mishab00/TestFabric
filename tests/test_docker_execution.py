from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import io
import tarfile
import unittest
from unittest.mock import patch

from docker.tls import TLSConfig

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import DockerRuntime, RepoSnapshot, RunContext, StageContext
from testfabric.execution.executors.base import BuildRequest, ExecutableCommand
from testfabric.execution.executors.docker import DockerExecutorAdapter
from testfabric.execution.executors.dockerops import docker_client, docker_run, prepare_endpoint
from testfabric.orchestrator.stage.builder import StageBuilder


class _FakeDockerExecutor:
    def __init__(self) -> None:
        self.calls: list[BuildRequest] = []

    def build(self, ctx, req, *, on_line=None) -> None:
        self.calls.append(req)
        if on_line:
            on_line("building layer\n")


class _FakeRegistry:
    def __init__(self, docker_executor) -> None:
        self.docker_executor = docker_executor

    def get(self, name: str):
        if name != "docker":
            raise AssertionError(f"unexpected executor lookup: {name}")
        return self.docker_executor


class DockerExecutionTests(unittest.TestCase):
    def _make_ctx(self, td: str) -> StageContext:
        root = Path(td)
        repo_dir = root / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")

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
        stage_ref = StageRef(index=1, title="Docker", suite="smoke")
        ctx = StageContext(
            run=run_ctx,
            suite_name="smoke",
            stage_index=1,
            stage_title="Docker",
            stage_slug=stage_ref.slug,
            stage_ref=stage_ref,
            executor="docker",
            runner="command",
            kind="command",
            build=False,
            max_workers=1,
            chunk_size=1,
            max_retries=0,
            worker_id="w001",
            docker=DockerRuntime(
                image="managed:ci",
                mode="template",
                repo_mount="/workspace",
                workdir="/workspace",
            ),
        )
        paths.ensure_run_dirs()
        paths.ensure_worker_tmp_dirs(ctx.worker_id)
        paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)
        return ctx

    def test_stage_builder_creates_docker_build_log_and_caches_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_ctx(td)
            fake_executor = _FakeDockerExecutor()
            builder = StageBuilder(executors=_FakeRegistry(fake_executor))

            builder.build_if_needed(ctx)
            builder.build_if_needed(ctx)

            self.assertEqual(len(fake_executor.calls), 1)
            req = fake_executor.calls[0]
            self.assertTrue(req.dockerfile.startswith(".testfabric/docker/managed-"))
            log_path = Path(ctx.stage_logs_dir) / "docker-build.log"
            self.assertTrue(log_path.exists())
            body = log_path.read_text(encoding="utf-8")
            self.assertIn("=== BUILD START", body)
            self.assertIn("building layer", body)
            self.assertIn("=== BUILD END ===", body)

    def test_docker_executor_run_forwards_mounts_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_ctx(td)
            artifacts_dir = Path(td) / "host-artifacts"
            cmd = ExecutableCommand(
                cmd=["pytest", "-q"],
                env={"TOKEN": "abc"},
                workdir="tests/e2e",
                workdir_repo=True,
                artifacts_dir=str(artifacts_dir),
                artifacts_root="/artifacts",
                network="host",
                shm_size="1g",
                timeout_seconds=15,
            )

            with patch(
                "testfabric.execution.executors.docker.docker_run",
                return_value=SimpleNamespace(exit_code=124, stdout="out", stderr="err"),
            ) as docker_run:
                result = DockerExecutorAdapter().run(ctx, cmd)

            self.assertTrue(artifacts_dir.exists())
            self.assertEqual(result.exit_code, 124)
            self.assertTrue(result.timed_out)

            docker_run.assert_called_once()
            kwargs = docker_run.call_args.kwargs
            self.assertEqual(kwargs["image"], "managed:ci")
            self.assertEqual(kwargs["repo_path"], ctx.repo_path)
            self.assertEqual(kwargs["repo_mount"], "/workspace")
            self.assertEqual(kwargs["workdir"], "/workspace/tests/e2e")
            self.assertEqual(kwargs["artifacts_dir"], str(artifacts_dir))
            self.assertEqual(kwargs["endpoint"], {})
            self.assertEqual(kwargs["timeout_seconds"], 15)
            self.assertEqual(kwargs["env"]["TOKEN"], "abc")

    def test_docker_executor_run_forwards_remote_docker_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = self._make_ctx(td)
            object.__setattr__(ctx, "docker_endpoint", {"mode": "ssh", "base_url": "ssh://docker-user@builder-1", "use_ssh_client": "true"})
            cmd = ExecutableCommand(
                cmd=["pytest", "-q"],
                env={},
                workdir=".",
                workdir_repo=True,
                artifacts_dir=str(Path(td) / "host-artifacts"),
                artifacts_root="/artifacts",
            )

            with patch(
                "testfabric.execution.executors.docker.docker_run",
                return_value=SimpleNamespace(exit_code=0, stdout="", stderr=""),
            ) as docker_run:
                DockerExecutorAdapter().run(ctx, cmd)

            kwargs = docker_run.call_args.kwargs
            self.assertEqual(kwargs["endpoint"]["mode"], "ssh")
            self.assertEqual(kwargs["endpoint"]["base_url"], "ssh://docker-user@builder-1")

    def test_prepare_endpoint_builds_temp_remote_docker_transport_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key_path = root / "id_testfabric"
            known_hosts = root / "known_hosts"
            key_path.write_text("dummy-key\n", encoding="utf-8")
            known_hosts.write_text("builder-1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest\n", encoding="utf-8")

            prepared = prepare_endpoint(
                {
                    "mode": "ssh",
                    "base_url": "ssh://docker-user@builder-1:2222",
                    "host": "builder-1",
                    "user": "docker-user",
                    "port": "2222",
                    "key_path": str(key_path),
                    "known_hosts": str(known_hosts),
                    "via": "jump-a",
                }
            )

            self.assertIsNotNone(prepared.temp_home)
            self.assertIsNotNone(prepared.cleanup_dir)
            self.assertEqual(prepared.client_kwargs["use_ssh_client"], True)
            self.assertEqual(prepared.client_kwargs["base_url"], "ssh://builder-1")

            control_dir = Path(str(prepared.temp_home)) / ".ssh"
            config_body = (control_dir / "config").read_text(encoding="utf-8")
            self.assertIn("HostName builder-1", config_body)
            self.assertIn("User docker-user", config_body)
            self.assertIn("Port 2222", config_body)
            self.assertIn(f"IdentityFile {key_path}", config_body)
            self.assertIn("ProxyCommand ssh -W %h:%p jump-a", config_body)
            self.assertTrue((control_dir / "known_hosts").exists())

    def test_prepare_endpoint_builds_tls_config_for_https_remote_docker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ca_cert = root / "ca.pem"
            client_cert = root / "client.pem"
            client_key = root / "client-key.pem"
            ca_cert.write_text("ca\n", encoding="utf-8")
            client_cert.write_text("cert\n", encoding="utf-8")
            client_key.write_text("key\n", encoding="utf-8")

            prepared = prepare_endpoint(
                {
                    "base_url": "https://builder-1.example.com:2376",
                    "tls": {
                        "verify": True,
                        "ca_cert": str(ca_cert),
                        "client_cert": str(client_cert),
                        "client_key": str(client_key),
                    },
                }
            )

        self.assertEqual(prepared.client_kwargs["base_url"], "https://builder-1.example.com:2376")
        self.assertIsInstance(prepared.client_kwargs["tls"], TLSConfig)

    def test_docker_client_keeps_temp_home_active_for_remote_docker_session(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key_path = root / "id_testfabric"
            known_hosts = root / "known_hosts"
            key_path.write_text("dummy-key\n", encoding="utf-8")
            known_hosts.write_text("builder-1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest\n", encoding="utf-8")
            observed_home: list[str | None] = []

            class _FakeClient:
                def __init__(self) -> None:
                    self.close_called = False

                def close(self) -> None:
                    self.close_called = True

            with patch("testfabric.execution.executors.dockerops.docker.DockerClient", return_value=_FakeClient()):
                with docker_client(
                    {
                        "mode": "ssh",
                        "base_url": "ssh://docker-user@builder-1:2222",
                        "host": "builder-1",
                        "user": "docker-user",
                        "port": "2222",
                        "key_path": str(key_path),
                        "known_hosts": str(known_hosts),
                        "via": "jump-a",
                    }
                ) as client:
                    observed_home.append(os.environ.get("HOME"))
                    self.assertTrue(hasattr(client, "close"))

            self.assertTrue(observed_home[0])
            self.assertNotEqual(observed_home[0], os.path.expanduser("~"))

    def test_docker_run_remote_docker_endpoint_uses_archives_instead_of_host_mounts(self) -> None:
        class _FakeContainer:
            def __init__(self) -> None:
                self.id = "c123"
                self.created_kwargs = None
                self.put_calls = []
                self.removed = False

            def start(self):
                return None

            def wait(self):
                return {"StatusCode": 0}

            def logs(self, stream=True, stdout=True, stderr=True, follow=False):
                if stdout and not stderr:
                    return b"out\n"
                if stderr and not stdout:
                    return b"err\n"
                return b"out\nerr\n"

            def put_archive(self, path, data):
                self.put_calls.append((path, data))
                return True

            def get_archive(self, path):
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode="w") as tar:
                    body = b"remote-data\n"
                    info = tarfile.TarInfo(name="artifact.txt")
                    info.size = len(body)
                    tar.addfile(info, io.BytesIO(body))
                payload.seek(0)
                return [payload.getvalue()], {"name": path}

            def remove(self, force=True):
                self.removed = True

        class _FakeContainers:
            def __init__(self, container):
                self.container = container
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                self.container.created_kwargs = kwargs
                return self.container

        class _FakeClient:
            def __init__(self, container):
                self.containers = _FakeContainers(container)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo_dir.mkdir()
            (repo_dir / "sample.txt").write_text("repo-data\n", encoding="utf-8")
            artifacts_dir = root / "artifacts"
            fake_container = _FakeContainer()
            fake_client = _FakeClient(fake_container)

            with patch("testfabric.execution.executors.dockerops.docker_client") as docker_client:
                docker_client.return_value.__enter__.return_value = fake_client
                docker_client.return_value.__exit__.return_value = None
                result = docker_run(
                    image="managed:ci",
                    repo_path=str(repo_dir),
                    repo_mount="/workspace",
                    cmd_inside=["sh", "-lc", "echo ok"],
                    env={},
                    artifacts_dir=str(artifacts_dir),
                    workdir="/workspace",
                    network=None,
                    shm_size=None,
                    keep_container=False,
                    endpoint={"mode": "ssh", "base_url": "ssh://docker-user@builder-1", "use_ssh_client": "true"},
                )

            self.assertEqual(result.exit_code, 0)
            create_kwargs = fake_container.created_kwargs
            self.assertIsNone(create_kwargs["volumes"])
            self.assertEqual(len(fake_container.put_calls), 1)
            self.assertTrue((artifacts_dir / "artifact.txt").exists())
            self.assertEqual((artifacts_dir / "artifact.txt").read_text(encoding="utf-8"), "remote-data\n")
            self.assertTrue(fake_container.removed)


if __name__ == "__main__":
    unittest.main()
