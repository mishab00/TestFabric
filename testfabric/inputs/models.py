from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


InputType = Literal["string", "bool", "int", "path", "secret"]


class InputDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: InputType = "string"
    required: bool = False
    default: Any | None = None
    env: str | None = None
    file: str | None = None
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_shorthand(cls, data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, dict):
            return data
        return {"default": data}


class ProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    overlay: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_profile(cls, data: Any) -> Any:
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise TypeError("profile definition must be a mapping")

        raw = dict(data)
        overlay = dict(raw.get("overlay") or {})

        for key in list(raw.keys()):
            if key in {"description", "inputs", "overlay"}:
                continue
            overlay[key] = raw.pop(key)

        raw["overlay"] = overlay
        return raw


class ResolvedInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def as_expansion_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in self.values.items():
            if value is None:
                continue
            text = self._stringify(value)
            out[f"inputs.{key}"] = text
        return out
