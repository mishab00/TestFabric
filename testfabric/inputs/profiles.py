from __future__ import annotations

from copy import deepcopy
from typing import Any

from testfabric.inputs.models import ProfileDefinition


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def apply_profile_overlay(
    raw_spec: dict[str, Any],
    *,
    profile_name: str | None = None,
) -> tuple[dict[str, Any], ProfileDefinition | None]:
    data = deepcopy(raw_spec)
    if not profile_name:
        return data, None

    profiles_raw = dict(data.get("profiles") or {})
    if profile_name not in profiles_raw:
        raise ValueError(f"Unknown profile '{profile_name}'. Available: {sorted(profiles_raw.keys())}")

    profile = ProfileDefinition.model_validate(profiles_raw[profile_name])
    if profile.overlay:
        data = deep_merge(data, profile.overlay)
    return data, profile
