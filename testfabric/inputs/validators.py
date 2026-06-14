from __future__ import annotations

from pathlib import Path
from typing import Any

from testfabric.inputs.models import InputDefinition


class InputValidationError(ValueError):
    pass


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def coerce_input_value(name: str, definition: InputDefinition, value: Any) -> Any:
    if value is None:
        return None

    kind = definition.type

    if kind in {"string", "secret"}:
        return str(value)

    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
        raise InputValidationError(f"input '{name}' expected a bool, got {value!r}")

    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise InputValidationError(f"input '{name}' expected an int, got {value!r}") from e

    if kind == "path":
        return str(Path(str(value)).expanduser())

    raise InputValidationError(f"input '{name}' has unsupported type {kind!r}")
