from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from testfabric.cli.main import app
from testfabric.cli.config import CONFIG_ENV


class CliConfigTests(unittest.TestCase):
    def test_config_add_use_list_and_show_round_trip(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.yaml"
            env = {CONFIG_ENV: str(config_path)}

            add_result = runner.invoke(
                app,
                [
                    "config",
                    "add",
                    "team-staging",
                    "--mode",
                    "remote",
                    "--api-url",
                    "http://localhost:5678",
                    "--team",
                    "platform",
                    "--project",
                    "api-gateway",
                    "--workspace",
                    "/workspaces/api-gateway",
                ],
                env=env,
            )
            self.assertEqual(add_result.exit_code, 0, add_result.output)
            self.assertTrue(config_path.exists())
            self.assertIn("saved context: team-staging", add_result.output)

            list_result = runner.invoke(app, ["config", "list"], env=env)
            self.assertEqual(list_result.exit_code, 0, list_result.output)
            self.assertIn("team-staging", list_result.output)
            self.assertIn("local", list_result.output)
            self.assertIn("*", list_result.output)

            use_result = runner.invoke(app, ["config", "use", "team-staging"], env=env)
            self.assertEqual(use_result.exit_code, 0, use_result.output)
            self.assertIn("active context: team-staging", use_result.output)

            show_result = runner.invoke(app, ["config", "show"], env=env)
            self.assertEqual(show_result.exit_code, 0, show_result.output)
            self.assertIn("name: team-staging", show_result.output)
            self.assertIn("mode: remote", show_result.output)
            self.assertIn("api_url: http://localhost:5678", show_result.output)
            self.assertIn("team: platform", show_result.output)
            self.assertIn("project: api-gateway", show_result.output)
            self.assertIn("active: true", show_result.output)

    def test_config_show_defaults_to_local_without_saved_config(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.yaml"
            env = {CONFIG_ENV: str(config_path)}

            show_result = runner.invoke(app, ["config", "show"], env=env)
            self.assertEqual(show_result.exit_code, 0, show_result.output)
            self.assertIn("name: local", show_result.output)
            self.assertIn("mode: local", show_result.output)
            self.assertIn("active: true", show_result.output)


if __name__ == "__main__":
    unittest.main()
