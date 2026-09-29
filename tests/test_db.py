"""Tests for the database layer.

The PostgreSQL path is tested where it can be tested without a server: URL
parsing, driver detection, the SQL translation shim and the migration runner's
bookkeeping all run against SQLite. A live PostgreSQL round-trip is skipped
unless a server is configured, so the suite passes on a clean checkout.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from app import config
from app.db import backend, migrate
from app.db.backend import PostgresConnection, SqliteConnection
from app.db.check import check as run_check
from app.store import Store


class ConfigTests(unittest.TestCase):
    def test_default_is_the_bundled_sqlite_file(self) -> None:
        current = config.settings()
        self.assertTrue(current.is_sqlite)
        self.assertFalse(current.is_postgres)

    def test_password_is_redacted_from_the_display_url(self) -> None:
        current = config.settings(
            {"db_url": "postgresql://formu:secret@db.example.com:5432/formusense"}
        )
        self.assertEqual(current.redacted_db_url(), "postgresql://formu:***@db.example.com:5432/formusense")
        self.assertTrue(current.is_postgres)

    def test_sqlite_path_is_recovered_from_a_url(self) -> None:
        self.assertEqual(
            config.sqlite_path_from_url("sqlite:///C:/data/x.db"), Path("C:/data/x.db")
        )
        self.assertIsNone(config.sqlite_path_from_url("sqlite:///:memory:"))
        self.assertIsNone(config.sqlite_path_from_url("postgresql://h/db"))


class BackendTests(unittest.TestCase):
    def test_statement_splitter_keeps_only_real_statements(self) -> None:
        statements = backend._split_statements("CREATE TABLE t (a int);\n-- comment\n;\nCREATE INDEX i ON t(a);")
        self.assertEqual(len(statements), 2)
        self.assertTrue(statements[0].startswith("CREATE TABLE"))
        self.assertTrue(statements[1].startswith("CREATE INDEX"))

    def test_postgres_translation_rewrites_the_formulation_upsert(self) -> None:
        translated = PostgresConnection._translate(
            "INSERT OR REPLACE INTO formulations (product_id, version) VALUES (?,?)"
        )
        self.assertTrue(translated.startswith("INSERT INTO formulations"))
        self.assertIn("ON CONFLICT (product_id, version) DO UPDATE", translated)
        self.assertIn("%s", translated)
        self.assertNotIn("?", translated)

    def test_lastval_is_only_probed_for_tables_that_own_a_sequence(self) -> None:
        # Regression: the migration runner records itself in schema_migrations, which
        # has no sequence. LASTVAL() raises there, and in PostgreSQL a raised statement
        # aborts the transaction it runs in - so the probe has to be refused outright
        # rather than attempted and rolled back. Every fresh PostgreSQL startup failed
        # on this while the SQLite path stayed green, which is why CI caught it and the
        # local suite could not.
        self.assertFalse(
            backend._needs_lastval("INSERT INTO schema_migrations (filename, applied_at) VALUES (%s,%s)")
        )
        self.assertFalse(backend._needs_lastval("SELECT LASTVAL()"))
        self.assertFalse(backend._needs_lastval("INSERT INTO products (name) VALUES (%s) RETURNING id"))
        for table in sorted(backend.SEQUENCED_TABLES):
            self.assertTrue(
                backend._needs_lastval(f"INSERT INTO {table} (a) VALUES (%s)"),
                f"{table} owns a sequence, so its id has to be read back",
            )
        # The translated formulation upsert keeps probing, exactly as before.
        upsert = PostgresConnection._translate(
            "INSERT OR REPLACE INTO formulations (product_id, version) VALUES (?,?)"
        )
        self.assertTrue(backend._needs_lastval(upsert))

    def test_every_table_the_store_inserts_into_is_known_to_be_sequenced(self) -> None:
        # If the store starts inserting into a new table, the id read-back has to learn
        # about it or the insert quietly stops returning an id on PostgreSQL.
        source = (Path(__file__).resolve().parent.parent / "app" / "store.py").read_text(encoding="utf-8")
        inserted = {
            name.lower()
            for name in re.findall(r"INSERT\s+(?:OR\s+REPLACE\s+)?INTO\s+([a-z_]+)", source, re.IGNORECASE)
        }
        self.assertTrue(inserted, "no INSERT statements found in app/store.py")
        self.assertLessEqual(inserted, set(backend.SEQUENCED_TABLES))

    def test_a_comment_header_does_not_swallow_the_first_statement(self) -> None:
        # Regression: the migration files open with a `--` header, and a naive
        # splitter skipped the whole chunk including the first CREATE TABLE.
        sql = (migrate.MIGRATIONS_DIR / "postgres" / "0001_init.sql").read_text(encoding="utf-8")
        statements = backend._split_statements(sql)
        joined = "\n".join(statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS products", joined)
        self.assertNotIn("FormuSense initial schema", joined)
        self.assertGreaterEqual(len(statements), 9)

    def test_the_inserted_id_is_attached_to_a_cursor_of_our_own(self) -> None:
        # Regression: the id used to be assigned onto the driver's cursor, but
        # psycopg's cursor declares __slots__ = () and refuses the attribute. Inside
        # the probe's try block that AttributeError was swallowed, so app/store.py
        # then failed on `int(cursor.lastrowid)` - on PostgreSQL only, which is why
        # the local suite stayed green while CI went red.
        conn = _postgres_connection([{"lastval": 7}])
        cursor = conn.execute("INSERT INTO products (name) VALUES (?)", ("pg test",))
        self.assertIsInstance(cursor, backend.Cursor)
        self.assertEqual(cursor.lastrowid, 7)

    def test_a_postgres_insert_still_asks_the_sequence_for_the_id(self) -> None:
        conn = _postgres_connection([{"lastval": 3}])
        conn.execute("INSERT INTO products (name) VALUES (?)", ("pg test",))
        self.assertEqual(
            [sql for sql, _ in conn.conn.cursors[0].executed],
            ["INSERT INTO products (name) VALUES (%s)", "SELECT LASTVAL()"],
        )

    def test_a_plain_read_is_not_probed_for_a_lastrowid(self) -> None:
        conn = _postgres_connection([{"id": 1}])
        cursor = conn.execute("SELECT id FROM products WHERE id=?", (1,))
        self.assertEqual(
            [sql for sql, _ in conn.conn.cursors[0].executed], ["SELECT id FROM products WHERE id=%s"]
        )
        self.assertIsNone(cursor.lastrowid)

    def test_the_cursor_wrapper_forwards_to_the_driver_cursor(self) -> None:
        conn = _postgres_connection([{"id": 1}])
        cursor = conn.execute("SELECT id FROM products WHERE id=?", (1,))
        self.assertEqual(cursor.fetchone(), {"id": 1})
        self.assertEqual(cursor.fetchall(), [])
        self.assertEqual(cursor.rowcount, -1)
        self.assertEqual(list(cursor), [])
        cursor.close()

    def test_the_driver_cursor_really_cannot_hold_a_lastrowid(self) -> None:
        # While this raises, attaching the id anywhere but on our own cursor loses
        # it; if a future psycopg allows the attribute, the wrapper is redundant
        # rather than wrong, and this test is the note explaining why.
        with self.assertRaises(AttributeError):
            _SlottedCursor([]).lastrowid = 1

    def test_postgres_translation_leaves_plain_reads_alone(self) -> None:
        translated = PostgresConnection._translate("SELECT * FROM products WHERE id=?")
        self.assertEqual(translated, "SELECT * FROM products WHERE id=%s")

    def test_sqlite_driver_is_always_available(self) -> None:
        available, detail = backend.driver_available("sqlite:///anything.db")
        self.assertTrue(available)
        self.assertIn("sqlite", detail.lower())


class _SlottedCursor:
    """A stand-in for psycopg's cursor, which has no instance dictionary.

    The empty ``__slots__`` is the whole point: it makes ``cursor.lastrowid = id``
    raise, exactly as it does on the real driver, so the tests below fail if the
    shim ever goes back to attaching the id to the driver's own cursor.
    """

    __slots__ = ("executed", "_rows")

    def __init__(self, rows: list) -> None:
        self.executed: list = []
        self._rows = list(rows)

    def execute(self, sql: str, params: object = None) -> "_SlottedCursor":
        self.executed.append((sql, params))
        return self

    def fetchone(self) -> object:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list:
        rows, self._rows = self._rows, []
        return rows

    def close(self) -> None:
        pass

    def __iter__(self) -> object:
        # Iterating consumes the result set, as it does on the driver's cursor.
        while self._rows:
            yield self._rows.pop(0)

    @property
    def rowcount(self) -> int:
        return -1


class _FakeConnection:
    """Just enough of a psycopg connection to reach PostgresConnection.execute."""

    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.cursors: list = []

    def cursor(self) -> _SlottedCursor:
        cursor = _SlottedCursor(self.rows)
        self.cursors.append(cursor)
        return cursor


def _postgres_connection(rows: list) -> PostgresConnection:
    # The constructor would need a server; the execute path does not.
    conn = object.__new__(PostgresConnection)
    conn.conn = _FakeConnection(rows)
    return conn


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-db-"))
        self.conn = SqliteConnection(self.tmp / "t.db")

    def tearDown(self) -> None:
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrations_apply_and_are_idempotent(self) -> None:
        first = migrate.migrate(self.conn, "sqlite")
        self.assertIn("0001_init.sql", first["applied"])
        second = migrate.migrate(self.conn, "sqlite")
        self.assertEqual(second["applied"], [], "a second run must apply nothing")
        status = migrate.migration_status(self.conn, "sqlite")
        self.assertEqual(status["pending"], [])
        self.assertIn("0001_init.sql", status["applied"])

    def test_expected_tables_exist_after_migration(self) -> None:
        migrate.migrate(self.conn, "sqlite")
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row["name"] for row in rows}
        for table in ("products", "formulations", "trials", "predictions", "plans", "ledger", "schema_migrations"):
            self.assertIn(table, names)
        # The model layer's own table arrives in its own migration, so a checkout that
        # stopped at the first one would have an application that fails on its first
        # call rather than a database that is simply older.
        self.assertIn("ai_calls", names)

    def test_the_two_dialects_create_the_same_tables(self) -> None:
        # Two hand-written schemas drift. This is the cheap way to notice: if a table
        # is added to one dialect and not the other, the PostgreSQL job is the only
        # place it would otherwise show up.
        def tables(dialect: str):
            found = set()
            for path in sorted((migrate.MIGRATIONS_DIR / dialect).glob("*.sql")):
                found |= {
                    name.lower()
                    for name in re.findall(
                        r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)", path.read_text(encoding="utf-8"), re.I
                    )
                }
            return found

        sqlite_tables = tables("sqlite")
        postgres_tables = tables("postgres")
        self.assertIn("ai_calls", sqlite_tables)
        self.assertEqual(sqlite_tables, postgres_tables)

    def test_the_ai_ledger_can_be_written_and_read_back_on_either_dialect(self) -> None:
        # The row is written by hand here rather than through the store, because the
        # point is the schema: a NOT NULL column the store forgets to fill would fail
        # on PostgreSQL and pass on SQLite.
        migrate.migrate(self.conn, "sqlite")
        self.conn.execute(
            "INSERT INTO ai_calls (product_id, kind, provider, model, prompt_hash, text, cached, ms, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (None, "vision", "openrouter", "openai/gpt-4o-mini", "abc", "a description", 0, 12, "2026-01-01T00:00:00"),
        )
        row = self.conn.execute("SELECT kind, model FROM ai_calls").fetchone()
        self.assertEqual(row["kind"], "vision")
        self.assertEqual(row["model"], "openai/gpt-4o-mini")


class StoreBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-store-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_store_opened_by_path_uses_sqlite(self) -> None:
        store = Store(self.tmp / "by_path.db")
        try:
            self.assertEqual(store.dialect, "sqlite")
            self.assertTrue(store.path.exists())
            self.assertIn("0001_init.sql", store.migrations["applied"])
        finally:
            store.close()

    def test_a_store_opened_by_url_uses_the_backend(self) -> None:
        url = f"sqlite:///{(self.tmp / 'by_url.db').as_posix()}"
        store = Store(url=url)
        try:
            self.assertEqual(store.dialect, "sqlite")
            self.assertEqual(store.db_url, url)
        finally:
            store.close()

    def test_the_reference_schema_is_the_migration_file(self) -> None:
        # The DDL printed in the report must be the DDL that is actually created.
        import app.store as store_module

        self.assertIn("CREATE TABLE IF NOT EXISTS products", store_module.SCHEMA)

    @unittest.skipUnless(
        os.environ.get("FORMUSENSE_TEST_POSTGRES"),
        "set FORMUSENSE_TEST_POSTGRES to a PostgreSQL URL to run the live round-trip",
    )
    def test_live_postgres_round_trip(self) -> None:
        store = Store(url=os.environ["FORMUSENSE_TEST_POSTGRES"])
        try:
            self.assertEqual(store.dialect, "postgres")
            product_id = store.create_product(
                __import__("app.core.brief", fromlist=["build_brief"]).build_brief(
                    {"product_name": "pg test", "category": "cookie", "spec_text": ""}
                )
            )
            self.assertGreater(product_id, 0)
            self.assertIsNotNone(store.product(product_id))
            store.clear(product_id)
        finally:
            store.close()


class RecordCheckTests(unittest.TestCase):
    """The deployment check: does the record layer work on this database at all?"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="formusense-check-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_check_walks_every_operation_the_record_layer_has(self) -> None:
        result = run_check(f"sqlite:///{(self.tmp / 'check.db').as_posix()}")
        self.assertTrue(result["ok"], result["failure"])
        steps = [step["step"] for step in result["steps"]]
        for expected in (
            "migrations",
            "product",
            "upsert",
            "prediction",
            "trial",
            "diagnosis",
            "plan",
            "ledger",
            "benchmark",
            "delete",
        ):
            self.assertTrue(any(expected in name for name in steps), f"no step mentions {expected}: {steps}")
        self.assertTrue(all(step["ok"] for step in result["steps"]))

    def test_the_check_leaves_the_record_the_way_it_found_it(self) -> None:
        # It is pointed at a deployment's database, so it has to be a read: leaving a
        # made-up benchmark behind would make the interface report a comparison that
        # nobody ran, as the latest one on record.
        url = f"sqlite:///{(self.tmp / 'clean.db').as_posix()}"
        store = Store(url=url)
        try:
            store.save_benchmark({"summary": {"agent_successes": 9}})
        finally:
            store.close()

        self.assertTrue(run_check(url)["ok"])

        store = Store(url=url)
        try:
            self.assertEqual(len(store.products()), 0, "the check left a product behind")
            latest = store.latest_benchmark()
            self.assertEqual(latest["summary"]["agent_successes"], 9, "the check replaced the stored benchmark")
        finally:
            store.close()

    def test_a_failure_names_the_step_and_carries_the_traceback(self) -> None:
        # The reason the command exists: on a hosted job the traceback sits in a log
        # that needs authentication to open, so the failure has to be quotable.
        result = run_check(f"sqlite:///{self.tmp.as_posix()}")  # a directory, not a file
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["step"], "open the store and apply the migrations")
        self.assertIn("Error", result["failure"]["type"])
        self.assertIn("Traceback", result["failure"]["traceback"])
        self.assertTrue(result["steps"])
        self.assertFalse(result["steps"][-1]["ok"])


if __name__ == "__main__":
    unittest.main()
