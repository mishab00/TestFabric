from __future__ import annotations

import textwrap
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from testfabric.orchestrator.plan.compiler import compile_run_plan
from testfabric.spec.schema import RunSpec


class RunSpecResolutionTests(unittest.TestCase):
    def test_load_resolved_applies_profile_inputs_and_cli_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    inputs:
                      env_name:
                        required: true
                      retries:
                        type: int
                        default: 1

                    profiles:
                      remote:
                        inputs:
                          env_name: remote
                        run:
                          ref: "${inputs.env_name}"

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
                        - suite: smoke

                    suites:
                      smoke:
                        kind: pytest
                        path: tests
                        extra_env:
                          TARGET_ENV: "${inputs.env_name}"

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: "${inputs.retries}"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec, resolved_inputs = RunSpec.load_resolved(
                str(spec_path),
                profile="remote",
                inputs_map={"retries": "7"},
            )

        self.assertEqual(spec.run.ref, "remote")
        self.assertEqual(spec.suites["smoke"].extra_env["TARGET_ENV"], "remote")
        self.assertEqual(spec.parallelism.max_retries, 7)
        self.assertEqual(resolved_inputs.values["env_name"], "remote")
        self.assertEqual(resolved_inputs.values["retries"], 7)

    def test_load_resolves_env_backed_inputs_only_when_declared(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    inputs:
                      token:
                        required: true
                        env: TOKEN

                    run:
                      name: sample
                      repo_url: git@example.com:org/repo.git
                      ref: main
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        kind: pytest
                        path: tests
                        extra_env:
                          TOKEN: "${inputs.token?missing token input}"

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"TOKEN": "XXXX"}, clear=False):
                spec = RunSpec.load(str(spec_path))

        self.assertEqual(spec.suites["smoke"].extra_env["TOKEN"], "XXXX")

    def test_load_resolves_multiple_declared_env_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    inputs:
                      token:
                        required: true
                        env: TOKEN
                      artifactory_api_key:
                        type: secret
                        required: true
                        env: ARTIFACTORY_API_KEY
                      registry_password:
                        type: secret
                        required: true
                        env: PASSWORD

                    run:
                      name: sample
                      repo_url: git@example.com:org/repo.git
                      ref: main
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        kind: pytest
                        path: tests
                        args: "--token=${inputs.token}"
                        extra_env:
                          TOKEN: "${inputs.token}"
                          REGISTRY_PASSWORD: "${inputs.registry_password}"

                    docker:
                      build_args:
                        ARTIFACTORY_API_KEY: "${inputs.artifactory_api_key}"

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "TOKEN": "XXXX",
                    "ARTIFACTORY_API_KEY": "XXXX",
                    "PASSWORD": "XXXX",
                },
                clear=False,
            ):
                spec, resolved_inputs = RunSpec.load_resolved(str(spec_path))

        self.assertEqual(resolved_inputs.values["token"], "XXXX")
        self.assertEqual(resolved_inputs.values["artifactory_api_key"], "XXXX")
        self.assertEqual(resolved_inputs.values["registry_password"], "XXXX")
        self.assertEqual(spec.suites["smoke"].args, "--token=XXXX")
        self.assertEqual(spec.suites["smoke"].extra_env["TOKEN"], "XXXX")
        self.assertEqual(spec.suites["smoke"].extra_env["REGISTRY_PASSWORD"], "XXXX")
        self.assertEqual(spec.docker.build_args["ARTIFACTORY_API_KEY"], "XXXX")

    def test_load_does_not_expand_raw_os_environment_without_inputs(self) -> None:
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
                      max_workers: 1

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        kind: pytest
                        path: tests
                        extra_env:
                          TOKEN: "${TOKEN?missing TOKEN}"

                    parallelism:
                      max_workers: 1
                      chunk_size: 1
                      max_retries: 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"TOKEN": "XXXX"}, clear=False):
                with self.assertRaisesRegex(ValueError, "missing TOKEN"):
                    RunSpec.load(str(spec_path))

    def test_load_rejects_unknown_run_and_stage_keys_early(self) -> None:
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
                      artifcats_dir: ./typo-artifacts

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - suite: smoke
                          executro: local

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

            with self.assertRaises(ValidationError) as exc:
                RunSpec.load(str(spec_path))

        msg = str(exc.exception)
        self.assertIn("artifcats_dir", msg)
        self.assertIn("executro", msg)

    def test_minimal_command_spec_uses_local_friendly_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      repo_url: .

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: hello
                            cmd: ["bash", "-lc", "echo hello"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))

        self.assertEqual(spec.run.workdir, ".testfabric/work")
        self.assertEqual(spec.parallelism.max_workers, 1)
        self.assertIsNone(spec.workers.max_workers)

    def test_run_section_can_be_omitted_for_local_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        steps:
                          - name: hello
                            cmd: ["echo", "hello"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))

        self.assertEqual(spec.run.repo_url, ".")
        self.assertEqual(spec.run.workdir, ".testfabric/work")
        self.assertEqual(spec.run.artifacts_dir, "artifacts")

    def test_omitted_worker_capacity_derives_from_stage_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    pipeline:
                      stages:
                        - suite: smoke
                          max_workers: 5

                    suites:
                      smoke:
                        steps:
                          - name: hello
                            cmd: ["echo", "hello"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertIsNone(spec.workers.max_workers)
        self.assertEqual(spec.effective_worker_capacity(), 5)
        self.assertEqual(plan.stages[0].max_workers, 5)

    def test_omitted_worker_capacity_derives_from_global_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    parallelism:
                      max_workers: 3

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        steps:
                          - name: hello
                            cmd: ["echo", "hello"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertIsNone(spec.workers.max_workers)
        self.assertEqual(spec.parallelism.max_workers, 3)
        self.assertEqual(spec.effective_worker_capacity(), 3)
        self.assertEqual(plan.stages[0].max_workers, 3)

    def test_load_preserves_runtime_shell_env_references_in_command_steps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      repo_url: .

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: write
                            cmd:
                              - bash
                              - -lc
                              - echo "$TESTFABRIC_ARTIFACTS_DIR" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/out.txt"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))

        cmd = spec.suites["smoke"].steps[0].cmd[2]
        self.assertIn("$TESTFABRIC_ARTIFACTS_DIR", cmd)
        self.assertIn("$TESTFABRIC_JOB_ARTIFACTS_DIR", cmd)

    def test_load_infers_command_kind_from_steps_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      repo_url: .

                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        steps:
                          - name: hello
                            cmd: ["bash", "-lc", "echo hello"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertEqual(spec.suites["smoke"].kind, "command")
        self.assertEqual(plan.stages[0].kind, "command")
        self.assertEqual(plan.stages[0].executor, "local")

    def test_load_supports_bash_and_shell_command_step_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        steps:
                          - name: bash-step
                            bash:
                              - echo "from bash"
                              - echo "$TESTFABRIC_ARTIFACTS_DIR"
                          - name: shell-step
                            shell: echo "from sh"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))

        self.assertEqual(spec.suites["smoke"].steps[0].bash, 'echo "from bash"\necho "$TESTFABRIC_ARTIFACTS_DIR"')
        self.assertEqual(spec.suites["smoke"].steps[1].shell, 'echo "from sh"')

    def test_load_supports_output_capture_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        steps:
                          - name: hello
                            cmd: ["echo", "hello"]
                            stdout_to: out/$TESTFABRIC_JOB_ID.txt
                            stderr_to: logs/error.txt
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            spec = RunSpec.load(str(spec_path))

        step = spec.suites["smoke"].steps[0]
        self.assertEqual(step.stdout_to, "out/$TESTFABRIC_JOB_ID.txt")
        self.assertEqual(step.stderr_to, "logs/error.txt")

    def test_load_rejects_output_capture_paths_that_escape_artifacts_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    pipeline:
                      stages:
                        - suite: smoke

                    suites:
                      smoke:
                        steps:
                          - name: hello
                            cmd: ["echo", "hello"]
                            stdout_to: ../escape.txt
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValidationError, "capture path must not escape TESTFABRIC_ARTIFACTS_DIR"):
                RunSpec.load(str(spec_path))

    def test_remote_docker_example_loads_with_grouped_targets(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_remote_docker.yaml"))

        self.assertIn("builders", spec.target_group_map())
        self.assertIn("docker-a", spec.target_group_map()["builders"])
        self.assertEqual(spec.credential_map()["docker_remote"].user, "docker-user")
        stage = spec.pipeline.stages[0]
        self.assertEqual(stage.executor, "docker")
        self.assertEqual(stage.targets, "builders")

    def test_remote_docker_multi_example_loads_with_two_stage_targets(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = RunSpec.load(str(repo_root / "run_remote_docker_multi.yaml"))

        self.assertIn("builders", spec.target_group_map())
        self.assertEqual(set(spec.target_group_map()["builders"]), {"builder-a", "builder-b"})
        self.assertEqual(spec.pipeline.stages[0].targets, "builder-a")
        self.assertEqual(spec.pipeline.stages[1].targets, "builder-b")

    def test_watch_sections_load_and_compile_with_run_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      name: sample
                      repo_url: .
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    watch:
                      phases: [run]
                      sources:
                        - name: session_out
                          type: session
                          phases: [run]
                        - name: api_health
                          type: http
                          phases: [setup, run]
                          url: http://service/api/health
                          interval_seconds: 5
                      watchers:
                        - name: fail_on_traceback
                          source: session_out
                          phases: [run]
                          when:
                            match: Traceback
                          then:
                            fail: immediate
                        - name: ready_on_200
                          source: api_health
                          phases: [setup, run]
                          when:
                            status: 200
                          then:
                            report:
                              message: API health reported as ready
                        - name: report_on_degraded
                          source: api_health
                          phases: [setup, run]
                          when:
                            status: 500
                            match: degraded
                          then:
                            report:
                              message: API health reported a degraded state

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: observe
                          suite: smoke
                          executor: local

                    suites:
                      smoke:
                        kind: command
                        steps:
                          - name: emit_flow
                            cmd:
                              - python3
                              - -u
                              - -c
                              - |
                                print("job started", flush=True)
                                print("job processed", flush=True)
                                print("job finished", flush=True)

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
            plan = compile_run_plan(spec, run_id="run-001", mode="run")

        self.assertEqual(len(plan.stages), 1)
        self.assertIn("watch", plan.model_dump())
        self.assertEqual(plan.watch["phases"], ["run"])
        self.assertEqual(len(plan.watch["sources"]), 2)
        self.assertEqual(len(plan.watch["watchers"]), 3)
        self.assertEqual(plan.watch["watchers"][0]["then"]["fail"], "immediate")
        self.assertEqual(plan.watch["watchers"][1]["then"]["report"]["message"], "API health reported as ready")

    def test_watch_remote_file_and_metric_thresholds_load(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      name: sample
                      repo_url: .
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    watch:
                      phases: [run]
                      sources:
                        - name: remote_log
                          type: remote_file
                          phases: [stage]
                          target: remote-log-host
                          path: /var/log/testfabric-watch.log
                        - name: cpu_metric
                          type: metric
                          phases: [job]
                          mode: poll
                          interval_seconds: 1
                          shell: echo "95.5"
                      watchers:
                        - name: remote_hit
                          source: remote_log
                          phases: [stage]
                          when:
                            match: WATCH-HIT
                          then:
                            report:
                              message: remote hit
                        - name: cpu_high
                          source: cpu_metric
                          phases: [job]
                          when:
                            gt: 90
                          then:
                            report:
                              message: cpu high

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: observe
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

        self.assertEqual(spec.watch.sources[0].type, "remote_file")
        self.assertEqual(spec.watch.sources[0].target, "remote-log-host")
        self.assertEqual(spec.watch.watchers[0].when.match, "WATCH-HIT")
        self.assertEqual(spec.watch.watchers[1].when.gt, 90.0)
        self.assertEqual(spec.watch.sources[1].type, "metric")
        self.assertEqual(spec.watch.sources[1].shell, 'echo "95.5"')

    def test_watch_remote_metric_target_loads_from_inventory(self) -> None:
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
                    run:
                      name: sample
                      repo_url: .
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    targets:
                      file: inventory/targets.yaml

                    credentials:
                      file: inventory/credentials.yaml

                    watch:
                      phases: [run]
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

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: observe
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

        self.assertEqual(spec.watch.sources[0].type, "metric")
        self.assertEqual(spec.watch.sources[0].target, "remote-metric-host")
        self.assertEqual(spec.watch.sources[0].shell, 'echo "92.5"')

    def test_watch_sequence_loads_for_ordered_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      name: sample
                      repo_url: .
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    watch:
                      phases: [run]
                      sources:
                        - name: command_out
                          type: session
                          phases: [stage]
                          stream: stdout
                      watchers:
                        - name: job_flow_complete
                          source: command_out
                          phases: [stage]
                          when:
                            sequence:
                              - job started
                              - job processed
                              - job finished
                          then:
                            report:
                              message: job flow complete

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: observe
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

        self.assertEqual(spec.watch.watchers[0].when.sequence, ["job started", "job processed", "job finished"])
        self.assertEqual(spec.watch.watchers[0].then.report.message, "job flow complete")

    def test_command_suite_loads_interactive_prompt_steps_with_local_runner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec_path = Path(td) / "run.yaml"
            spec_path.write_text(
                textwrap.dedent(
                    """
                    run:
                      name: sample
                      repo_url: .
                      workdir: ./work
                      artifacts_dir: ./artifacts

                    workers:
                      mode: local
                      max_workers: 1

                    pipeline:
                      stages:
                        - title: prompt session
                          suite: prompt
                          executor: local

                    suites:
                      prompt:
                        kind: command
                        cmd:
                          - python3
                          - -u
                          - -c
                          - |
                            import sys
                            print("Password:", flush=True)
                            sys.stdin.readline()
                            print("done", flush=True)
                        expect:
                          - name: reply
                            when:
                              match: "Password:"
                            then:
                              send: XXXX

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
            plan = compile_run_plan(spec, run_id="run-002", mode="run")

        self.assertEqual(len(plan.stages), 1)
        self.assertEqual(plan.stages[0].runner, "command")
        self.assertEqual(plan.stages[0].executor, "local")


if __name__ == "__main__":
    unittest.main()
