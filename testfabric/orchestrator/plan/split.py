from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SplitSpec:
    count: int = 1
    manifest_path: str | None = None

    @property
    def enabled(self) -> bool:
        if self.manifest_path:
            return True
        return int(self.count) > 1
