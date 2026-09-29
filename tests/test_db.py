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

    def test_postgres_translation_leaves_plain_reads_alone(self) -> None:
        translated = PostgresConnection._translate("SELECT * FROM products WHERE id=?")
        self.assertEqual(translated, "SELECT * FROM products WHERE id=%s")

    def test_sqlite_driver_is_always_available(self) -> None:
        available, detail = backend.driver_available("sqlite:///anything.db")
        self.assertTrue(available)
        self.assertIn("sqlite", detail.lower())


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


if __name__ == "__main__":
    unittest.main()
