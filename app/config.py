"""Runtime configuration, read from the environment with safe defaults.

The offline promise is the default: with no environment set, the application uses
the bundled SQLite file and needs nothing installed. Pointing
``FORMUSENSE_DB_URL`` at a PostgreSQL server switches the same code onto a real
database server; the driver is imported lazily, so a machine without it still
runs the SQLite path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = ROOT / "data" / "formusense.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


@dataclass(frozen=True)
class Settings:
    db_url: str
    host: str
    port: int
    auth_token: str
    log_level: str
    log_requests: bool

    @property
    def is_postgres(self) -> bool:
        return self.db_url.startswith(("postgres://", "postgresql://"))

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite:")

    def redacted_db_url(self) -> str:
        """The database URL with any password removed, safe to log or display."""
        if "@" not in self.db_url:
            return self.db_url
        scheme, _, rest = self.db_url.partition("://")
        credentials, _, host = rest.rpartition("@")
        user = credentials.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"

    def as_dict(self) -> Dict[str, object]:
        return {
            "db_url": self.redacted_db_url(),
            "database": "postgresql" if self.is_postgres else "sqlite",
            "host": self.host,
            "port": self.port,
            "auth_required": bool(self.auth_token),
            "log_level": self.log_level,
        }


def settings(overrides: Optional[Dict[str, object]] = None) -> Settings:
    values = {
        "db_url": _env("FORMUSENSE_DB_URL", DEFAULT_DB_URL),
        "host": _env("FORMUSENSE_HOST", "127.0.0.1"),
        "port": int(_env("FORMUSENSE_PORT", "8770") or 8770),
        "auth_token": _env("FORMUSENSE_AUTH_TOKEN"),
        "log_level": _env("FORMUSENSE_LOG_LEVEL", "INFO").upper(),
        "log_requests": _env("FORMUSENSE_LOG_REQUESTS", "1") not in ("0", "false", "False"),
    }
    if overrides:
        values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)  # type: ignore[arg-type]


def sqlite_path_from_url(url: str) -> Optional[Path]:
    if not url.startswith("sqlite:"):
        return None
    _, _, tail = url.partition("sqlite:///")
    if not tail or tail == ":memory:":
        return None
    return Path(tail)
