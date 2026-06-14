from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from testfabric.inputs.models import ProfileDefinition
from testfabric.inputs.resolver import InputResolutionError, InputResolver


class InputResolverTests(unittest.TestCase):
    def test_resolve_inputs_from_defaults_profile_env_file_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            token_file = Path(td) / "token.txt"
            token_file.write_text("file-secret\n", encoding="utf-8")

            resolver = InputResolver()
            profile = ProfileDefinition.model_validate({"inputs": {"env_name": "remote"}})

            resolved = resolver.resolve(
                input_defs={
                    "env_name": {"required": True},
                    "enabled": {"type": "bool", "default": "false"},
                    "workers": {"type": "int", "default": "3"},
                    "config_path": {"type": "path", "default": "~/project"},
                    "api_token": {"type": "secret", "file": str(token_file), "env": "API_TOKEN"},
                },
                profile=profile,
                cli_inputs={"workers": "5"},
                env={"API_TOKEN": "XXXX"},
            )

        self.assertEqual(resolved.values["env_name"], "remote")
        self.assertIs(resolved.values["enabled"], False)
        self.assertEqual(resolved.values["workers"], 5)
        self.assertTrue(str(resolved.values["config_path"]).endswith("/project"))
        self.assertEqual(resolved.values["api_token"], "XXXX")

        self.assertEqual(resolved.sources["env_name"], "profile")
        self.assertEqual(resolved.sources["enabled"], "default")
        self.assertEqual(resolved.sources["workers"], "cli")
        self.assertEqual(resolved.sources["api_token"], "env")

    def test_missing_required_input_raises(self) -> None:
        resolver = InputResolver()
        with self.assertRaises(InputResolutionError):
            resolver.resolve(
                input_defs={"env_name": {"required": True}},
                profile=None,
                cli_inputs={},
                env={},
            )

    def test_env_resolution_requires_explicit_mapping(self) -> None:
        resolver = InputResolver()

        with self.assertRaises(InputResolutionError):
            resolver.resolve(
                input_defs={"token": {"required": True}},
                profile=None,
                cli_inputs={},
                env={"TOKEN": "XXXX"},
            )

        resolved = resolver.resolve(
            input_defs={"token": {"required": True, "env": "TOKEN"}},
            profile=None,
            cli_inputs={},
            env={"TOKEN": "XXXX"},
        )

        self.assertEqual(resolved.values["token"], "XXXX")
        self.assertEqual(resolved.sources["token"], "env")


if __name__ == "__main__":
    unittest.main()
