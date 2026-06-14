from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from testfabric.core.constants import DEFAULT_CONTEXT_NAME


CONFIG_ENV = "TESTFABRIC_CONFIG_PATH"
ContextMode = Literal["local", "remote"]


class ContextProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    mode: ContextMode = "remote"
    api_url: str | None = None
    token: str | None = None
    team: str | None = None
    project: str | None = None
    workspace: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CLIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_context: str | None = None
    contexts: dict[str, ContextProfile] = Field(default_factory=dict)


def default_config_path() -> Path:
    override = str(os.environ.get(CONFIG_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".testfabric" / "config.yaml").expanduser().resolve()


def default_config() -> CLIConfig:
    local = ContextProfile(name=DEFAULT_CONTEXT_NAME, mode="local")
    return CLIConfig(active_context=DEFAULT_CONTEXT_NAME, contexts={DEFAULT_CONTEXT_NAME: local})


def _normalize_config(config: CLIConfig) -> CLIConfig:
    data = config.model_dump(exclude_none=True)
    contexts = dict(data.get("contexts") or {})
    if DEFAULT_CONTEXT_NAME not in contexts:
        contexts[DEFAULT_CONTEXT_NAME] = ContextProfile(name=DEFAULT_CONTEXT_NAME, mode="local").model_dump(exclude_none=True)
    active = str(data.get("active_context") or "").strip()
    if not active or active not in contexts:
        active = DEFAULT_CONTEXT_NAME if DEFAULT_CONTEXT_NAME in contexts else (next(iter(contexts)) if contexts else DEFAULT_CONTEXT_NAME)
    return CLIConfig.model_validate({"active_context": active, "contexts": contexts})


def load_config(path: str | Path | None = None) -> CLIConfig:
    config_path = Path(path).expanduser().resolve() if path else default_config_path()
    if not config_path.exists():
        return default_config()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return default_config()
    try:
        config = CLIConfig.model_validate(payload)
    except Exception:
        return default_config()
    return _normalize_config(config)


def save_config(config: CLIConfig, path: str | Path | None = None) -> Path:
    config_path = Path(path).expanduser().resolve() if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_config(config)
    config_path.write_text(
        yaml.safe_dump(normalized.model_dump(exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    return config_path


def get_context(config: CLIConfig, name: str | None = None) -> ContextProfile:
    context_name = str(name or config.active_context or DEFAULT_CONTEXT_NAME).strip() or DEFAULT_CONTEXT_NAME
    if context_name == DEFAULT_CONTEXT_NAME and context_name not in config.contexts:
        return ContextProfile(name=DEFAULT_CONTEXT_NAME, mode="local")
    if context_name not in config.contexts:
        raise KeyError(f"Unknown context: {context_name}")
    return config.contexts[context_name]


def upsert_context(config: CLIConfig, context: ContextProfile) -> CLIConfig:
    updated = dict(config.contexts)
    updated[context.name] = context
    return _normalize_config(
        CLIConfig(
            active_context=config.active_context,
            contexts=updated,
        )
    )


def set_active_context(config: CLIConfig, name: str) -> CLIConfig:
    if name != DEFAULT_CONTEXT_NAME and name not in config.contexts:
        raise KeyError(f"Unknown context: {name}")
    return _normalize_config(
        CLIConfig(
            active_context=name,
            contexts=dict(config.contexts),
        )
    )


def context_rows(config: CLIConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    active = config.active_context or DEFAULT_CONTEXT_NAME
    for name in sorted(config.contexts):
        ctx = config.contexts[name]
        rows.append(
            {
                "active": "*" if name == active else "",
                "name": name,
                "mode": ctx.mode,
                "api_url": ctx.api_url or "-",
                "team": ctx.team or "-",
                "project": ctx.project or "-",
                "workspace": ctx.workspace or "-",
            }
        )
    if DEFAULT_CONTEXT_NAME not in config.contexts:
        rows.insert(
            0,
            {
                "active": "*" if active == DEFAULT_CONTEXT_NAME else "",
                "name": DEFAULT_CONTEXT_NAME,
                "mode": "local",
                "api_url": "-",
                "team": "-",
                "project": "-",
                "workspace": "-",
            },
        )
    return rows
