"""Database access: one dialect adapter in front of SQLite and PostgreSQL.

The record layer talks to a connection with a tiny, fixed surface (``execute``,
``executescript``, ``commit``, ``close``), so the same queries run on both
engines. SQLite is the zero-install default; PostgreSQL is the production target,
reached by setting ``FORMUSENSE_DB_URL``.
"""
from __future__ import annotations

from . import backend, migrate
from .backend import Connection, connect, driver_available

# The runner is re-exported under a verb, not under the module's own name: a
# ``from .migrate import migrate`` here would rebind ``app.db.migrate`` to the
# function and hide the module behind it, so callers could not reach
# ``migrate.MIGRATIONS_DIR`` or ``migrate.migration_status``.
from .migrate import migrate as apply_migrations
from .migrate import migration_status

__all__ = [
    "backend",
    "migrate",
    "Connection",
    "connect",
    "driver_available",
    "apply_migrations",
    "migration_status",
]
