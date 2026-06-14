from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from testfabric.orchestrator.orchestrator import Orchestrator, RunOptions
from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.orchestrator.stage.builder import StageBuilder
from testfabric.health.models import HealthCheckResult
from testfabric.health.probes_docker import DockerHealthProbeSuite
from testfabric.spec.schema import RunSpec
from testfabric.execution.executors.base import ExecResult
from testfabric.execution.executors.docker import DockerExecutorAdapter


class InventoryTargetTests(unittest.TestCase):
    def test_compile_run_plan_expands_remote_docker_targets_with_stage_selector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local
                      max_workers: 2

                    credentials:
                      docker_remote:
                        user: docker-user
                        port: 2222

                    targets:
                      builders:
                        docker-a:
                          address: 1.1.1.1
                          credential: docker_remote
                        docker-b:
                          address: 1.1.1.1
                          credential: docker_remote

                    pipeline:
                      stages:
                        - title: docker smoke
                          suite: smoke
                          executor: docker
                          targets: builders

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 2)
        self.assertEqual([stage.target_id for stage in plan.stages], ["docker-a", "docker-b"])
        self.assertEqual(plan.stages[0].docker_endpoint["mode"], "ssh")
        self.assertEqual(plan.stages[0].docker_endpoint["base_url"], "ssh://docker-user@1.1.1.1:2222")
        self.assertEqual(plan.stages[0].docker_endpoint["host"], "1.1.1.1")
        self.assertEqual(plan.stages[0].docker_endpoint["user"], "docker-user")
        self.assertEqual(plan.stages[0].docker_endpoint["port"], "2222")
        self.assertEqual(plan.stages[0].transport_type, "ssh")

    def test_compile_run_plan_remote_docker_endpoint_includes_credential_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local

                    credentials:
                      docker_remote:
                        user: docker-user
                        port: 2222
                        key_path: /keys/base.pem
                        known_hosts: /keys/known_hosts
                        via: jump-a

                    targets:
                      builders:
                        docker-a:
                          address: 1.1.1.1
                          credential: docker_remote
                          transport:
                            user: alt-user
                            via: null

                    pipeline:
                      stages:
                        - title: docker smoke
                          suite: smoke
                          executor: docker
                          targets: builders

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 1)
        endpoint = plan.stages[0].docker_endpoint
        self.assertEqual(endpoint["base_url"], "ssh://alt-user@1.1.1.1:2222")
        self.assertEqual(endpoint["user"], "alt-user")
        self.assertEqual(endpoint["key_path"], "/keys/base.pem")
        self.assertEqual(endpoint["known_hosts"], "/keys/known_hosts")
        self.assertEqual(endpoint["via"], "")

    def test_compile_run_plan_respects_explicit_remote_docker_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local

                    credentials:
                      remote_daemon:
                        base_url: https://builder-1.example.com:2376
                        tls: true

                    targets:
                      builders:
                        builder-1:
                          address: builder-1.example.com
                          credential: remote_daemon

                    pipeline:
                      stages:
                        - title: remote daemon
                          suite: smoke
                          executor: docker
                          targets: builders

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 1)
        endpoint = plan.stages[0].docker_endpoint
        self.assertEqual(endpoint["mode"], "https")
        self.assertEqual(endpoint["base_url"], "https://builder-1.example.com:2376")
        self.assertEqual(plan.stages[0].transport_type, "https")

    def test_compile_run_plan_infers_https_remote_docker_from_tls_settings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local

                    credentials:
                      remote_daemon:
                        tls: true
                        port: 2376

                    targets:
                      builders:
                        builder-1:
                          address: builder-1.example.com
                          credential: remote_daemon

                    pipeline:
                      stages:
                        - title: remote daemon
                          suite: smoke
                          executor: docker
                          targets: builders

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 1)
        endpoint = plan.stages[0].docker_endpoint
        self.assertEqual(endpoint["mode"], "https")
        self.assertEqual(endpoint["base_url"], "https://builder-1.example.com")

    def test_compile_run_plan_expands_top_level_grouped_targets_with_stage_selector(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: grouped-targets
                      repo_url: .
                      workdir: {str(root / "work")}
                      artifacts_dir: {str(root / "artifacts")}

                    workers:
                      mode: local
                      max_workers: 4

                    targets:
                      clients:
                        client-1:
                          address: 1.1.1.1
                          labels:
                            role: client
                        client-2:
                          address: 1.1.1.1
                      servers:
                        server-1:
                          address: 1.1.1.1

                    pipeline:
                      stages:
                        - title: smoke clients
                          suite: smoke
                          executor: docker
                          targets: clients

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 2)
        self.assertEqual([stage.target_id for stage in plan.stages], ["client-1", "client-2"])
        self.assertEqual([stage.target_group for stage in plan.stages], ["clients", "clients"])
        self.assertEqual(plan.stages[0].target_address, "1.1.1.1")
        self.assertEqual(plan.stages[0].target_labels["role"], "client")
        self.assertEqual(plan.stages[0].executor, "docker")
        self.assertEqual(plan.stages[0].target_kind, "docker-target")

    def test_load_resolves_targets_and_credentials_files_relative_to_spec(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inv_dir = root / "inventory"
            inv_dir.mkdir(parents=True, exist_ok=True)
            (inv_dir / "targets.yaml").write_text(
                textwrap.dedent(
                    """
                    clients:
                      client-1:
                        address: 1.1.1.1
                        credential: client_base
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (inv_dir / "credentials.yaml").write_text(
                textwrap.dedent(
                    """
                    client_base:
                      user: "${inputs.ssh_user}"
                      key_path: "${inputs.ssh_key_path}"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    inputs:
                      ssh_user:
                        required: true
                      ssh_key_path:
                        required: true

                    workers:
                      mode: local
                      max_workers: 2

                    targets:
                      file: inventory/targets.yaml

                    credentials:
                      file: inventory/credentials.yaml

                    pipeline:
                      stages:
                        - suite: smoke
                          executor: docker
                          targets: clients

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec, _ = RunSpec.load_resolved(
                str(spec_path),
                inputs_map={"ssh_user": "ubuntu", "ssh_key_path": "/tmp/id_rsa"},
            )

        self.assertIn("clients", spec.target_group_map())
        self.assertEqual(spec.target_group_map()["clients"]["client-1"].credential, "client_base")
        self.assertEqual(spec.credential_map()["client_base"].user, "ubuntu")
        self.assertEqual(spec.credential_map()["client_base"].key_path, "/tmp/id_rsa")

    def test_compile_run_plan_resolves_remote_file_source_target_from_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inv_dir = root / "inventory"
            inv_dir.mkdir(parents=True, exist_ok=True)
            (inv_dir / "targets.yaml").write_text(
                textwrap.dedent(
                    """
                    hosts:
                      remote-log-host:
                        address: 1.1.1.1
                        credential: remote-log-credential
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (inv_dir / "credentials.yaml").write_text(
                textwrap.dedent(
                    """
                    remote-log-credential:
                      user: root
                      key_path: /tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/id_ed25519
                      known_hosts: /tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/known_hosts
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local
                      max_workers: 1

                    targets:
                      file: inventory/targets.yaml

                    credentials:
                      file: inventory/credentials.yaml

                    watch:
                      phases: [stage]
                      sources:
                        - name: remote_app_log
                          type: remote_file
                          phases: [stage]
                          target: remote-log-host
                          path: /var/log/testfabric-watch.log
                      watchers:
                        - name: remote_file_contains_watch_hit
                          source: remote_app_log
                          when:
                            match: WATCH-HIT
                          then:
                            report:
                              message: remote hit

                    pipeline:
                      stages:
                        - title: observe remote file
                          suite: smoke
                          executor: local

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: ok
                            cmd: ["bash", "-lc", "echo ok"]

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
            plan = compile_run_plan(spec, run_id="run-remote-file", mode="dry-run")

        self.assertEqual(plan.watch["sources"][0]["type"], "remote_file")
        self.assertEqual(plan.watch["sources"][0]["target"], "remote-log-host")
        self.assertEqual(plan.watch["sources"][0]["host"], "1.1.1.1")
        self.assertEqual(plan.watch["sources"][0]["user"], "root")
        self.assertEqual(plan.watch["sources"][0]["key_path"], "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/id_ed25519")
        self.assertEqual(plan.watch["sources"][0]["known_hosts"], "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/known_hosts")

    def test_compile_run_plan_resolves_remote_metric_source_target_from_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inv_dir = root / "inventory"
            inv_dir.mkdir(parents=True, exist_ok=True)
            (inv_dir / "targets.yaml").write_text(
                textwrap.dedent(
                    """
                    hosts:
                      remote-metric-host:
                        address: 1.1.1.1
                        credential: remote-log-credential
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (inv_dir / "credentials.yaml").write_text(
                textwrap.dedent(
                    """
                    remote-log-credential:
                      user: root
                      key_path: /tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/id_ed25519
                      known_hosts: /tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/known_hosts
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local
                      max_workers: 1

                    targets:
                      file: inventory/targets.yaml

                    credentials:
                      file: inventory/credentials.yaml

                    watch:
                      phases: [stage]
                      sources:
                        - name: remote_cpu_metric
                          type: metric
                          phases: [stage]
                          target: remote-metric-host
                          mode: poll
                          interval_seconds: 1
                          shell: echo "92.5"
                      watchers:
                        - name: remote_metric_over_90
                          source: remote_cpu_metric
                          phases: [stage]
                          when:
                            gt: 90
                          then:
                            report:
                              message: remote metric high

                    pipeline:
                      stages:
                        - title: observe remote metric
                          suite: smoke
                          executor: local

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: ok
                            cmd: ["bash", "-lc", "echo ok"]

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
            plan = compile_run_plan(spec, run_id="run-remote-metric", mode="dry-run")

        self.assertEqual(plan.watch["sources"][0]["type"], "metric")
        self.assertEqual(plan.watch["sources"][0]["target"], "remote-metric-host")
        self.assertEqual(plan.watch["sources"][0]["host"], "1.1.1.1")
        self.assertEqual(plan.watch["sources"][0]["user"], "root")
        self.assertEqual(plan.watch["sources"][0]["key_path"], "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/id_ed25519")
        self.assertEqual(plan.watch["sources"][0]["known_hosts"], "/tmp/testfabric-bootstrap/remote-docker/1-1-1-1-22-root/known_hosts")

    def test_compile_run_plan_merges_worker_transport_credential_and_host_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    workers:
                      mode: local
                      max_workers: 2
                      transport:
                        user: root
                        port: 22
                        known_hosts: /etc/ssh/known_hosts

                    credentials:
                      base_key:
                        key_path: /keys/default.pem
                        via: jump-a

                    targets:
                      clients:
                        client-1:
                          address: 1.1.1.1
                          credential: base_key
                          transport:
                            user: ubuntu
                            via: null

                    pipeline:
                      stages:
                        - suite: smoke
                          executor: docker
                          targets: clients

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 1)
        stage = plan.stages[0]
        self.assertEqual(stage.target_credential, "base_key")
        endpoint = stage.docker_endpoint
        self.assertEqual(endpoint["user"], "ubuntu")
        self.assertEqual(endpoint["port"], "22")
        self.assertEqual(endpoint["known_hosts"], "/etc/ssh/known_hosts")
        self.assertEqual(endpoint["key_path"], "/keys/default.pem")
        self.assertEqual(endpoint["via"], "")

    def test_compile_run_plan_expands_group_selector_into_remote_docker_stages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inventory_path = root / "inventory.yaml"
            inventory_path.write_text(
                textwrap.dedent(
                    """
                    hosts:
                      client-a:
                        address: 1.1.1.1
                        groups: [workers]
                        labels:
                          role: client
                          region: eu
                      client-b:
                        address: 1.1.1.1
                        groups: [workers]
                        labels:
                          role: client
                          region: us
                      db-a:
                        address: 1.1.1.1
                        groups: [db]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: inventory-dry-run
                      repo_url: .
                      workdir: {str(root / "work")}
                      artifacts_dir: {str(root / "artifacts")}

                    workers:
                      mode: local
                      max_workers: 4

                    targets:
                      file: {inventory_path.name}

                    pipeline:
                      stages:
                        - title: smoke
                          suite: smoke
                          executor: docker
                          targets: workers

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="dry-run")

        self.assertEqual(len(plan.stages), 2)
        self.assertEqual([stage.index for stage in plan.stages], [1, 2])
        self.assertEqual([stage.target_id for stage in plan.stages], ["client-a", "client-b"])
        self.assertEqual([stage.target_name for stage in plan.stages], ["client-a", "client-b"])
        self.assertEqual([stage.target_address for stage in plan.stages], ["1.1.1.1", "1.1.1.1"])
        self.assertEqual([stage.target_group for stage in plan.stages], ["workers", "workers"])
        self.assertEqual(plan.stages[0].target_labels["role"], "client")
        self.assertEqual(plan.stages[1].target_labels["region"], "us")
        self.assertTrue(all(stage.executor == "docker" for stage in plan.stages))
        self.assertTrue(all(stage.target_kind == "docker-target" for stage in plan.stages))
        self.assertTrue(all(stage.transport_type == "ssh" for stage in plan.stages))

    def test_orchestrator_dry_run_with_remote_docker_inventory_reports_multiple_targets(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inventory_path = root / "inventory.yaml"
            inventory_path.write_text(
                textwrap.dedent(
                    """
                    all:
                      hosts:
                        py310:
                          ansible_host: 1.1.1.1
                          labels:
                            python: "3.10"
                      children:
                        matrix:
                          hosts:
                            py310: {}
                            py311:
                              ansible_host: 1.1.1.1
                              labels:
                                python: "3.11"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: remote-docker-inventory-dry-run
                      repo_url: {repo_root}
                      workdir: {str(root / "work")}
                      artifacts_dir: {str(root / "artifacts")}

                    workers:
                      mode: local
                      max_workers: 4

                    targets:
                      file: {inventory_path.name}

                    pipeline:
                      stages:
                        - title: collect-targets
                          suite: smoke
                          executor: docker
                          targets: matrix

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            with (
                patch.object(StageBuilder, "build_if_needed", lambda self, ctx: None),
                patch.object(
                    DockerHealthProbeSuite,
                    "probe",
                    return_value=[HealthCheckResult(name="docker", ok=True)],
                ) as probe_mock,
            ):
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, mode="dry-run", run_id="remote-docker-dry-run"),
                ).run()

            self.assertTrue(result["ok"], msg=result.get("error"))
            probe_mock.assert_called_once()
            self.assertFalse(probe_mock.call_args.kwargs["required"])
            self.assertEqual(len(probe_mock.call_args.kwargs["endpoints"]), 1)
            run_dir = Path(result["run_dir"])
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["totals"]["targets_total"], 2)
        self.assertEqual([item["target_id"] for item in summary["targets"]], ["py310", "py311"])
        self.assertEqual(summary["targets"][0]["kind"], "docker-target")
        self.assertEqual(summary["targets"][0]["scope"], "1.1.1.1")
        self.assertEqual(summary["targets"][0]["group"], "matrix")
        self.assertTrue(all(stage["executor"] == "docker" for stage in summary["stages"]))
        self.assertTrue(all(stage["transport"]["type"] == "ssh" for stage in summary["stages"]))
        self.assertEqual(summary["stages"][0]["target_address"], "1.1.1.1")
        self.assertEqual(summary["stages"][1]["target_address"], "1.1.1.1")

    def test_orchestrator_run_mode_executes_remote_docker_stage_with_mocked_adapter(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inventory_path = root / "inventory.yaml"
            inventory_path.write_text(
                textwrap.dedent(
                    """
                    hosts:
                      client-a:
                        address: 1.1.1.1
                        groups: [workers]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            spec_path = root / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    f"""
                    run:
                      name: remote-docker-run
                      repo_url: {repo_root}
                      workdir: {str(root / "work")}
                      artifacts_dir: {str(root / "artifacts")}

                    workers:
                      mode: local
                      max_workers: 1

                    targets:
                      file: {inventory_path.name}

                    pipeline:
                      stages:
                        - suite: smoke
                          executor: docker
                          targets: workers

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: run
                            shell: echo hello
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            def fake_build_if_needed(self, ctx):
                return None

            def fake_run(self, ctx, cmd, *, on_stdout=None, on_stderr=None):
                reports_dir = Path(str(cmd.artifacts_dir or "")).expanduser().resolve()
                reports_dir.mkdir(parents=True, exist_ok=True)
                (reports_dir / "remote.txt").write_text("ok\n", encoding="utf-8")
                if on_stdout:
                    on_stdout("remote ok\n")
                return ExecResult(exit_code=0, stdout="remote ok\n", stderr="")

            with (
                patch.object(StageBuilder, "build_if_needed", new=fake_build_if_needed),
                patch.object(
                    DockerHealthProbeSuite,
                    "probe",
                    return_value=[HealthCheckResult(name="docker", ok=True)],
                ) as probe_mock,
                patch.object(DockerExecutorAdapter, "run", new=fake_run),
            ):
                result = Orchestrator(
                    spec,
                    RunOptions.from_spec_and_cli(spec, mode="run", run_id="remote-docker-run"),
                ).run()
                summary = json.loads((Path(result["run_dir"]) / "summary.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], msg=result.get("error"))
        probe_mock.assert_called_once()
        self.assertEqual(summary["stages"][0]["executor"], "docker")
        self.assertEqual(summary["stages"][0]["target_id"], "client-a")
        self.assertEqual(summary["stages"][0]["artifacts"]["counts_by_category"]["artifact"], 1)
