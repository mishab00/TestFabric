from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Pattern


MASK = "***"

DEFAULT_SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


@dataclass(frozen=True)
class SecretRedactor:
    exact_values: tuple[str, ...] = field(default_factory=tuple)
    field_names: tuple[str, ...] = field(default_factory=tuple)
    regex_patterns: tuple[str | Pattern[str], ...] = field(default_factory=tuple)
    mask: str = MASK

    def __post_init__(self) -> None:
        normalized_fields = tuple(
            sorted(
                {self._normalize_field_name(name) for name in self.field_names if str(name or "").strip()}
            )
        )
        compiled = []
        for pattern in self.regex_patterns:
            if hasattr(pattern, "sub"):
                compiled.append(pattern)
            else:
                compiled.append(re.compile(str(pattern)))
        object.__setattr__(self, "field_names", normalized_fields)
        object.__setattr__(self, "_compiled_patterns", tuple(compiled))

    @classmethod
    def from_values(
        cls,
        values: list[str] | tuple[str, ...] | set[str] = (),
        *,
        field_names: list[str] | tuple[str, ...] | set[str] = (),
        regex_patterns: list[str | Pattern[str]] | tuple[str | Pattern[str], ...] | set[str | Pattern[str]] = (),
        include_default_field_names: bool = False,
        mask: str = MASK,
    ) -> "SecretRedactor":
        fields = set(field_names or ())
        if include_default_field_names:
            fields.update(DEFAULT_SECRET_FIELD_NAMES)
        return cls(
            exact_values=tuple(_normalized_values(values)),
            field_names=tuple(str(field) for field in fields),
            regex_patterns=tuple(regex_patterns or ()),
            mask=mask,
        )

    def with_values(self, values: list[str] | tuple[str, ...] | set[str]) -> "SecretRedactor":
        return SecretRedactor(
            exact_values=tuple(_normalized_values((*self.exact_values, *tuple(values or ())))),
            field_names=self.field_names,
            regex_patterns=self.regex_patterns,
            mask=self.mask,
        )

    def redact_text(self, text: Any) -> str:
        out = str(text or "")
        for value in _normalized_values(self.exact_values):
            out = out.replace(value, self.mask)
        for pattern in getattr(self, "_compiled_patterns", ()):
            out = pattern.sub(self.mask, out)
        return out

    def redact_data(self, data: Any) -> Any:
        if isinstance(data, str):
            return self.redact_text(data)
        if isinstance(data, dict):
            out: dict[Any, Any] = {}
            for key, value in data.items():
                if self._is_secret_field(key):
                    out[key] = self.mask
                else:
                    out[key] = self.redact_data(value)
            return out
        if isinstance(data, list):
            return [self.redact_data(v) for v in data]
        if isinstance(data, tuple):
            return tuple(self.redact_data(v) for v in data)
        if isinstance(data, set):
            return {self.redact_data(v) for v in data}
        return data

    def _is_secret_field(self, key: Any) -> bool:
        normalized = self._normalize_field_name(key)
        return bool(normalized and normalized in set(self.field_names))

    @staticmethod
    def _normalize_field_name(value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_")


def _normalized_values(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    out = []
    for value in values:
        text = str(value or "")
        if text:
            out.append(text)
    return sorted(set(out), key=len, reverse=True)


def redact_text(text: str, values: list[str] | tuple[str, ...] | set[str]) -> str:
    return SecretRedactor.from_values(values).redact_text(text)


def redact_data(data: Any, values: list[str] | tuple[str, ...] | set[str]) -> Any:
    return SecretRedactor.from_values(values).redact_data(data)


__all__ = [
    "DEFAULT_SECRET_FIELD_NAMES",
    "MASK",
    "SecretRedactor",
    "redact_data",
    "redact_text",
]
