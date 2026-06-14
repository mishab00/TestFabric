from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from testfabric.artifacts.paths import PathManager, StageRef
from testfabric.core.context import DockerRuntime, RepoSnapshot, RunContext, StageContext
from testfabric.execution.executors.local import LocalExecutorAdapter
from testfabric.execution.suites.base import JobPlan, TestItem
from testfabric.execution.suites.command import CommandRunnerAdapter


class ArtifactContractTests(unittest.TestCase):
    def test_stage_context_builds_local_and_docker_artifact_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
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
                repo=RepoSnapshot(repo_path=str(root / "repo"), commit_sha="abc123"),
            )
            stage_ref = StageRef(index=1, title="Tests", suite="smoke")

            local_ctx = StageContext(
                run=run_ctx,
                suite_name="smoke",
                stage_index=1,
                stage_title="Tests",
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
            )
            docker_ctx = StageContext(
                run=run_ctx,
                suite_name="smoke",
                stage_index=1,
                stage_title="Tests",
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
                    image="img",
                    repo_mount="/work",
                    workdir="/work",
                    env={},
                ),
            )

            local_env = local_ctx.artifact_env(job_id="job-1", attempt=2, artifacts_root=local_ctx.artifacts_root_in_env)
            docker_env = docker_ctx.artifact_env(job_id="job-1", attempt=2, artifacts_root=docker_ctx.artifacts_root_in_env)

            self.assertEqual(local_env["TESTFABRIC_RUN_ID"], "run-001")
            self.assertEqual(local_env["TESTFABRIC_STAGE_ID"], stage_ref.slug)
            self.assertTrue(local_env["TESTFABRIC_ARTIFACTS_DIR"].endswith("/reports"))
            self.assertTrue(local_env["TESTFABRIC_JOB_ARTIFACTS_DIR"].endswith("/artifacts/jobs/job-1/attempt-2"))

            self.assertEqual(docker_env["TESTFABRIC_ARTIFACTS_DIR"], "/artifacts")
            self.assertEqual(docker_env["TESTFABRIC_JOB_ARTIFACTS_DIR"], "/artifacts/artifacts/jobs/job-1/attempt-2")

    def test_local_command_job_can_write_artifacts_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)

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
            stage_ref = StageRef(index=1, title="Commands", suite="smoke")
            ctx = StageContext(
                run=run_ctx,
                suite_name="smoke",
                stage_index=1,
                stage_title="Commands",
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
            )
            paths.ensure_worker_tmp_dirs(ctx.worker_id)
            paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)

            suite_cfg = SimpleNamespace(
                kind="command",
                steps=[
                    SimpleNamespace(
                        name="write",
                        cmd=[
                            "bash",
                            "-lc",
                            'printf "artifact" > "$TESTFABRIC_ARTIFACTS_DIR/custom.txt" && '
                            'printf "artifact" > "$TESTFABRIC_JOB_ARTIFACTS_DIR/artifact.txt"',
                        ],
                        workdir=".",
                        env={},
                        continue_on_fail=False,
                        always=False,
                    )
                ],
                dry_steps=[],
                extra_env={},
            )
            job = JobPlan(job_id="job-001", items=[TestItem(id="write")])
            runner = CommandRunnerAdapter()
            cmd = runner.make_job_command(
                ctx,
                suite_cfg,
                job,
                attempt=0,
                artifacts_root=ctx.artifacts_root_in_env,
            )
            output_dir = Path(ctx.job_attempt_output_dir("job-001", 0))
            output_dir.mkdir(parents=True, exist_ok=True)

            cmd = type(cmd)(
                cmd=list(cmd.cmd),
                env=dict(cmd.env),
                workdir=cmd.workdir,
                workdir_repo=cmd.workdir_repo,
                artifacts_dir=ctx.stage_reports_dir,
                artifacts_root=cmd.artifacts_root,
                network=cmd.network,
                shm_size=cmd.shm_size,
                keep=cmd.keep,
                entrypoint=cmd.entrypoint,
            )

            result = LocalExecutorAdapter().run(ctx, cmd)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual((Path(ctx.stage_reports_dir) / "custom.txt").read_text(encoding="utf-8"), "artifact")
            self.assertEqual((output_dir / "artifact.txt").read_text(encoding="utf-8"), "artifact")

    def test_local_command_job_supports_bash_and_shell_step_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_dir = root / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)

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
            stage_ref = StageRef(index=1, title="Commands", suite="smoke")
            ctx = StageContext(
                run=run_ctx,
                suite_name="smoke",
                stage_index=1,
                stage_title="Commands",
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
            )
            paths.ensure_worker_tmp_dirs(ctx.worker_id)
            paths.ensure_worker_stage_dirs(ctx.worker_id, ctx.stage_ref)
            output_dir = Path(ctx.job_attempt_output_dir("job-001", 0))
            output_dir.mkdir(parents=True, exist_ok=True)

            suite_cfg = SimpleNamespace(
                kind="command",
                steps=[
                    SimpleNamespace(
                        name="write-bash",
                        bash=f'echo "bash-output" > "{Path(ctx.stage_reports_dir) / "bash.txt"}"',
                        shell=None,
                        workdir=".",
                        env={},
                        continue_on_fail=False,
                        always=False,
                    ),
                    SimpleNamespace(
                        name="write-shell",
                        bash=None,
                        shell=f'echo "shell-output" > "{output_dir / "shell.txt"}"',
                        workdir=".",
                        env={},
                        continue_on_fail=False,
                        always=False,
                    ),
                ],
                dry_steps=[],
                extra_env={},
            )
            job = JobPlan(job_id="job-001", items=[TestItem(id="write-bash"), TestItem(id="write-shell")])
            runner = CommandRunnerAdapter()
            cmd = runner.make_job_command(
                ctx,
                suite_cfg,
                job,
                attempt=0,
                artifacts_root=ctx.artifacts_root_in_env,
            )

            result = LocalExecutorAdapter().run(ctx, cmd)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual((Path(ctx.stage_reports_dir) / "bash.txt").read_text(encoding="utf-8").strip(), "bash-output")
            self.assertEqual((output_dir / "shell.txt").read_text(encoding="utf-8").strip(), "shell-output")


if __name__ == "__main__":
    unittest.main()
