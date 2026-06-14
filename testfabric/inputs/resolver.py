from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from testfabric.inputs.models import InputDefinition, ProfileDefinition, ResolvedInputs
from testfabric.inputs.validators import InputValidationError, coerce_input_value


class InputResolutionError(ValueError):
    pass


class InputResolver:
    def resolve(
        self,
        *,
        input_defs: Mapping[str, InputDefinition | dict[str, Any] | Any] | None,
        profile: ProfileDefinition | None = None,
        cli_inputs: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ResolvedInputs:
        env_map = dict(env or os.environ)
        cli_map = dict(cli_inputs or {})

        values: dict[str, Any] = {}
        sources: dict[str, str] = {}

        for name, raw_def in dict(input_defs or {}).items():
            definition = raw_def if isinstance(raw_def, InputDefinition) else InputDefinition.model_validate(raw_def)

            value: Any = None
            source = ""

            if definition.default is not None:
                value = definition.default
                source = "default"

            if profile is not None and name in profile.inputs:
                value = profile.inputs[name]
                source = "profile"

            file_path = self._resolve_file_path(definition, env_map)
            if file_path is not None:
                value = file_path.read_text(encoding="utf-8").rstrip("\r\n")
                source = "file"

            env_value = self._resolve_env_value(definition, env_map)
            if env_value is not None:
                value = env_value
                source = "env"

            if name in cli_map:
                value = cli_map[name]
                source = "cli"

            if value is None:
                if definition.required:
                    raise InputResolutionError(f"Missing required input: {name}")
                continue

            try:
                values[name] = coerce_input_value(name, definition, value)
            except InputValidationError as e:
                raise InputResolutionError(str(e)) from e
            sources[name] = source or "default"

        return ResolvedInputs(values=values, sources=sources)

    def _resolve_env_value(
        self,
        definition: InputDefinition,
        env_map: Mapping[str, str],
    ) -> str | None:
        if definition.env and definition.env in env_map:
            return env_map[definition.env]
        return None

    def _resolve_file_path(
        self,
        definition: InputDefinition,
        env_map: Mapping[str, str],
    ) -> Path | None:
        if not definition.file:
            return None

        raw = os.path.expandvars(str(definition.file))
        path = Path(raw).expanduser()
        if not path.exists():
            return None
        if not path.is_file():
            raise InputResolutionError(f"input file is not a regular file: {path}")
        return path
