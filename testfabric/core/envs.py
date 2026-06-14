from __future__ import annotations
import re
from typing import Mapping

_VAR = re.compile(r"\$(\w+)|\$\{([^}]+)\}")

def expand_vars(value: str | None, env: Mapping[str, str]) -> str:
    if value is None:
        return ""
    s = str(value)

    def repl(m: re.Match) -> str:
        k = m.group(1) or m.group(2) or ""
        return str(env.get(k, ""))  # missing -> empty
    return _VAR.sub(repl, s)

def expand_dict(d: Mapping[str, str] | None, env: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (d or {}).items():
        out[str(k)] = expand_vars(str(v), env)
    return out

def merge_env(*layers: Mapping[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for layer in layers:
        if not layer:
            continue
        for k, v in layer.items():
            if v is None:
                continue
            out[str(k)] = str(v)
    return out
