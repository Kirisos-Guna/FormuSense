"""Forward-only, versioned schema migrations.

Each dialect has its own directory of numbered ``.sql`` files. The runner applies
any file that has not been recorded in ``schema_migrations``, in filename order,
and records it. That makes the schema reproducible on a fresh PostgreSQL server
and idempotent on an existing SQLite file.

The runner is intentionally small and dependency-free: a migration toolchain
(Alembic and friends) would bring a dependency the offline story cannot carry.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def _directory(dialect: str) -> Path:
    return MIGRATIONS_DIR / dialect


def _applied(conn: Any) -> List[str]:
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    out: List[str] = []
    for row in rows:
        try:
            out.append(str(row["filename"]))
        except (TypeError, KeyError, IndexError):
            out.append(str(row[0]))
    return out


def _migration_files(dialect: str) -> List[Path]:
    directory = _directory(dialect)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.sql"))


def pending(conn: Any, dialect: str) -> List[Path]:
    done = set(_applied(conn))
    return [p for p in _migration_files(dialect) if p.name not in done]


def migrate(conn: Any, dialect: str) -> Dict[str, Any]:
    """Apply every pending migration. Safe to call on every startup."""
    conn.executescript(TRACKING_TABLE)
    conn.commit()
    applied: List[str] = []
    for path in pending(conn, dialect):
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (filename, applied_at) VALUES (?,?)",
            (path.name, time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()
        applied.append(path.name)
    return {
        "dialect": dialect,
        "applied": applied,
        "total": len(_migration_files(dialect)),
    }


def migration_status(conn: Any, dialect: str) -> Dict[str, Any]:
    conn.executescript(TRACKING_TABLE)
    conn.commit()
    done = set(_applied(conn))
    files = [p.name for p in _migration_files(dialect)]
    return {
        "dialect": dialect,
        "applied": sorted(done),
        "pending": [name for name in files if name not in done],
    }
