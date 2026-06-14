from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from testfabric.core.context import DockerRuntime
from testfabric.execution.executors.docker_template import build_request_for_runtime, resolve_runtime


class DockerTemplateTests(unittest.TestCase):
    def test_template_mode_without_python_inputs_does_not_force_pip_network_work(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)

            runtime = DockerRuntime(image="managed:ci", mode="template")
            req = build_request_for_runtime(str(repo), runtime)
            body = (repo / req.dockerfile).read_text(encoding="utf-8")

            self.assertIn("FROM python:3.11-slim", body)
            self.assertNotIn("RUN python -m pip install --upgrade pip", body)

    def test_auto_mode_uses_repo_dockerfile_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")

            runtime = DockerRuntime(image="testfabric-tests:ci", mode="auto")
            resolved = resolve_runtime(str(repo), runtime)
            req = build_request_for_runtime(str(repo), runtime)

            self.assertEqual(resolved.mode, "custom")
            self.assertEqual(req.dockerfile, "Dockerfile")
            self.assertTrue(req.image.startswith("testfabric-managed:"))

    def test_auto_mode_generates_managed_dockerfile_when_repo_has_no_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")

            runtime = DockerRuntime(image="testfabric-tests:ci", mode="auto")
            resolved = resolve_runtime(str(repo), runtime)
            req = build_request_for_runtime(str(repo), runtime)
            dockerfile_path = repo / req.dockerfile

            self.assertEqual(resolved.mode, "template")
            self.assertTrue(dockerfile_path.exists())
            body = dockerfile_path.read_text(encoding="utf-8")
            self.assertIn("FROM python:3.11-slim", body)
            self.assertIn("RUN python -m pip install --upgrade pip", body)
            self.assertIn("COPY requirements.txt /tmp/testfabric/requirements-1.txt", body)
            self.assertIn("RUN python -m pip install -r /tmp/testfabric/requirements-1.txt", body)

    def test_template_mode_supports_poetry_install_without_repo_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "pyproject.toml").write_text(
                "[tool.poetry]\nname = 'sample'\nversion = '0.1.0'\n",
                encoding="utf-8",
            )

            runtime = DockerRuntime(image="managed:ci", mode="template")
            req = build_request_for_runtime(str(repo), runtime)
            body = (repo / req.dockerfile).read_text(encoding="utf-8")

            self.assertIn("RUN python -m pip install poetry", body)
            self.assertIn("COPY pyproject.toml /tmp/testfabric/pyproject.toml", body)
            self.assertIn("poetry install --no-interaction --no-root", body)


if __name__ == "__main__":
    unittest.main()
