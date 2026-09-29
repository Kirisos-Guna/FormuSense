"""Connection backends: SQLite (standard library) and PostgreSQL (optional driver).

Both expose the same four calls the record layer uses, so :mod:`app.store` does
not care which one it is on:

    conn.execute(sql, params)      # returns a .Cursor: .fetchone/.fetchall/.lastrowid
    conn.executescript(sql)        # several statements, used for schema files
    conn.commit() / conn.close()
    conn.dialect                   # "sqlite" or "postgres"

The PostgreSQL backend imports its driver lazily. If the driver is not installed
the import error is turned into a readable instruction rather than a traceback at
startup, and the SQLite path is completely unaffected.

Three SQL differences are handled explicitly, and only these three, so the shim
stays reviewable:

* placeholder style: ``?`` (SQLite) versus ``%s`` (PostgreSQL);
* ``INSERT OR REPLACE`` on the versioned formulation table, which becomes
  ``INSERT ... ON CONFLICT (product_id, version) DO UPDATE``;
* ``lastrowid``, which PostgreSQL does not populate, so the id is read back with
  ``SELECT LASTVAL()`` immediately after an insert - but only for the tables that
  own a sequence, because ``LASTVAL()`` raises otherwise and a raised statement
  aborts the transaction it runs in;
* the cursor object itself, which is wrapped in :class:`Cursor` so that it carries
  a ``lastrowid``. psycopg's cursor declares ``__slots__ = ()`` and so has no
  instance dictionary, which means the id cannot be attached to the driver's own
  cursor object at all.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

INSERT_OR_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+formulations", re.IGNORECASE)
INSERT_TABLE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

#: The tables whose primary key is a sequence, so LASTVAL() can be asked for the
#: id of the row just inserted. Anything else - the migration bookkeeping table,
#: for instance - has no sequence, and asking raises: in PostgreSQL a raised
#: statement aborts the whole transaction, which is how every fresh PostgreSQL
#: startup used to fail while the SQLite path stayed green.
SEQUENCED_TABLES = frozenset(
    {
        "products",
        "formulations",
        "predictions",
        "trials",
        "analyses",
        "diagnoses",
        "plans",
        "ledger",
        "benchmarks",
    }
)


def _needs_lastval(statement: str) -> bool:
    """Whether the id of the inserted row has to be read back with LASTVAL().

    True only for an insert into a table that owns a sequence and that is not
    already returning its own row. The probe is refused rather than attempted and
    rolled back, because the failure of a statement poisons the transaction.
    """
    if "RETURNING" in statement.upper():
        return False
    match = INSERT_TABLE.match(statement)
    return bool(match) and match.group(1).lower() in SEQUENCED_TABLES


class Cursor:
    """The cursor the record layer gets back: a driver cursor plus ``lastrowid``.

    The driver's cursor is deliberately not returned. Both backends have to answer
    ``cursor.lastrowid`` after an insert, and psycopg's cursor cannot hold that
    attribute: ``Cursor`` declares ``__slots__ = ()``, so assigning to it raises
    ``AttributeError`` and the id of the row just inserted is lost - quietly, when
    the assignment sits inside a probe that is allowed to fail. Everything else is
    forwarded to the driver's cursor, which still owns the result set.
    """

    __slots__ = ("_cursor", "lastrowid")

    def __init__(self, cursor: Any, lastrowid: Optional[int] = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> Any:
        return self._cursor.fetchall()

    def fetchmany(self, size: Optional[int] = None) -> Any:
        return self._cursor.fetchmany() if size is None else self._cursor.fetchmany(size)

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self) -> Any:
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        # Reached only for names this wrapper does not define itself, so everything
        # else the driver cursor exposes (rowcount, description, ...) keeps working.
        if name == "_cursor":
            raise AttributeError(name)
        return getattr(self._cursor, name)


def _read_lastval(cursor: Any) -> Optional[int]:
    """The id of the row just inserted, asked of the sequence that produced it.

    ``LASTVAL()`` returns the value most recently produced by a sequence in this
    session, which straight after an insert into a ``SERIAL`` column is the id the
    caller wants. Only ever called for a table in :data:`SEQUENCED_TABLES`, because
    on any other table it raises and a raised statement aborts its transaction.
    """
    cursor.execute("SELECT LASTVAL()")
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return int(next(iter(row.values())))
    if isinstance(row, (list, tuple)):
        return int(row[0])
    return int(row)


POSTGRES_DRIVER_HINT = (
    "PostgreSQL support needs a driver. Install one with:\n"
    "    pip install \"psycopg[binary]\"\n"
    "or run without FORMUSENSE_DB_URL to use the bundled SQLite database."
)


class Connection:
    """The common surface both backends implement."""

    dialect = "sqlite"

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:  # pragma: no cover
        raise NotImplementedError

    def executescript(self, sql: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def commit(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


class SqliteConnection(Connection):
    dialect = "sqlite"

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
        else:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # The HTTP server is threaded and opens one store per request, so two
        # requests can want the write lock at the same moment. Write-ahead logging
        # plus a busy timeout turns that from an exception into a wait.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return self.conn.execute(sql, tuple(params or ()))

    def executescript(self, sql: str) -> None:
        self.conn.executescript(sql)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class PostgresConnection(Connection):
    dialect = "postgres"

    def __init__(self, url: str) -> None:
        try:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(POSTGRES_DRIVER_HINT) from exc
        self._psycopg = psycopg
        self.conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)

    # ------------------------------------------------------------ translation #
    @staticmethod
    def _translate(sql: str) -> str:
        if INSERT_OR_REPLACE.match(sql):
            # The only INSERT OR REPLACE in the schema is the formulation upsert,
            # which is keyed by the UNIQUE(product_id, version) constraint.
            sql = re.sub(
                r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+formulations",
                "INSERT INTO formulations",
                sql,
                flags=re.IGNORECASE,
            )
            sql = sql.rstrip().rstrip(";")
            sql += (
                " ON CONFLICT (product_id, version) DO UPDATE SET"
                " payload_json = EXCLUDED.payload_json,"
                " source = EXCLUDED.source,"
                " label = EXCLUDED.label,"
                " created_at = EXCLUDED.created_at"
            )
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        statement = self._translate(sql)
        cursor = self.conn.cursor()
        cursor.execute(statement, tuple(params or ()))
        # The id is read back on the same cursor rather than on a second one: the
        # caller of an insert never reads the insert's own result set.
        lastrowid = _read_lastval(cursor) if _needs_lastval(statement) else None
        return Cursor(cursor, lastrowid)

    def executescript(self, sql: str) -> None:
        cursor = self.conn.cursor()
        for statement in _split_statements(sql):
            cursor.execute(statement)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def _split_statements(sql: str) -> List[str]:
    """Split a schema script into statements.

    Schema files here contain no dollar-quoted bodies or semicolons inside string
    literals, so a plain split on ``;`` is sufficient and is asserted by a test.
    Comment lines are stripped *before* the emptiness test, not used as a reason
    to skip the chunk: a file whose header comment precedes the first CREATE
    TABLE would otherwise have that table silently dropped.
    """
    statements: List[str] = []
    for chunk in sql.split(";"):
        lines = [line for line in chunk.splitlines() if not line.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if not cleaned:
            continue
        statements.append(cleaned)
    return statements


def driver_available(url: str) -> Tuple[bool, str]:
    """Whether the driver for a URL is importable, and a human-readable reason."""
    if url.startswith("sqlite:"):
        return True, "sqlite3 (standard library)"
    if url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg  # type: ignore  # noqa: F401

            return True, "psycopg"
        except ImportError:
            return False, POSTGRES_DRIVER_HINT
    return False, f"unsupported database URL: {url}"


def connect(url: str) -> Connection:
    """Open a connection for a database URL."""
    if url.startswith("sqlite:"):
        _, _, tail = url.partition("sqlite:///")
        if not tail or tail == ":memory:":
            return SqliteConnection(None)
        return SqliteConnection(Path(tail))
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresConnection(url)
    raise ValueError(f"unsupported database URL: {url}")
