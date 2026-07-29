from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    definition_version: int

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            database_path=Path(os.environ.get("ORDER_DATABASE", "var/orders.sqlite3")),
            definition_version=int(os.environ.get("ORDER_DEFINITION_VERSION", "2")),
        )
