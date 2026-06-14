from __future__ import annotations

import re
from typing import Any, Mapping

_BRACED = re.compile(r"\$\{([^}]+)\}")


class ParametrizationError(ValueError):
    pass


def parse_assignment_list(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in list(items or []):
        s = str(raw).strip()
        if not s:
            continue
        if "=" not in s:
            raise ParametrizationError(f"Invalid assignment '{s}', expected KEY=VALUE")
        k, v = s.split("=", 1)
        key = k.strip()
        if not key:
            raise ParametrizationError(f"Invalid assignment '{s}', key is empty")
        out[key] = v
    return out


def _resolve_expr(expr: str, values: Mapping[str, str]) -> str:
    # ${NAME:-default}
    if ":-" in expr:
        name, default = expr.split(":-", 1)
        key = name.strip()
        got = values.get(key)
        return str(got) if got not in (None, "") else default

    # ${NAME?message}
    if "?" in expr:
        name, msg = expr.split("?", 1)
        key = name.strip()
        got = values.get(key)
        if got in (None, ""):
            text = msg.strip() or f"Missing required variable: {key}"
            raise ParametrizationError(text)
        return str(got)

    key = expr.strip()
    return str(values.get(key, ""))


def _expand_string(s: str, values: Mapping[str, str]) -> str:
    def repl_braced(m: re.Match[str]) -> str:
        return _resolve_expr(m.group(1), values)
    return _BRACED.sub(repl_braced, s)


def _walk_expand(node: Any, values: Mapping[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: _walk_expand(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_expand(x, values) for x in node]
    if isinstance(node, str):
        return _expand_string(node, values)
    return node


def expand_data(node: Any, values: Mapping[str, str]) -> Any:
    return _walk_expand(node, values)
