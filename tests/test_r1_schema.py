from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import hashlib
import json

from r1_support import AT, clock
from r0_support import ROOT
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import to_epoch_ms
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase, connect
from maibot_autonomous_planning_plugin.maivita.life.storage.schema import Migration, SchemaError, migrate, migrations, validate_schema


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name).resolve() / "maivita.db"
        self.clock = clock()

    def tearDown(self):
        self.temp.cleanup()

    def test_R1_schema_tables_and_constraints_are_real_not_empty_placeholders(self):
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"schema_migrations", "life_state", "activities", "plans", "schedule_items", "schedule_revisions",
                                      "sleep_sessions", "sleep_segments", "commitments", "life_goals", "life_preferences", "life_events",
                                      "event_deliveries", "command_receipts", "migration_runs", "legacy_import_records", "legacy_id_map", "decision_runs", "integration_settings", "event_subscriptions",
                                      "habits", "life_summaries", "retention_audit", "memory_deliveries", "activity_checkpoints", "decision_triggers", "plan_item_checks", "observation_gaps"})
            self.assertIn("decision_runs", tables)
            self.assertIn("habits", tables)
            self.assertNotIn("goals", tables)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("SELECT count(*) FROM life_state").fetchone()[0], 0)

    def test_migration_is_explicit_checksum_verified_and_idempotent(self):
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            before = tuple(conn.execute("SELECT version,checksum,applied_at_ms FROM schema_migrations").fetchone())
            self.assertEqual(before, (1, migrations()[0].checksum, to_epoch_ms(AT)))
            self.clock.set_utc(AT.replace(year=2027))
            self.assertEqual(migrate(conn, self.clock), len(migrations()))
            self.assertEqual(tuple(conn.execute("SELECT version,checksum,applied_at_ms FROM schema_migrations").fetchone()), before)
            self.assertEqual(validate_schema(conn), len(migrations()))

    def test_newer_schema_refuses_open_without_rewriting_version(self):
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            conn.execute("UPDATE schema_migrations SET version=99 WHERE version=1")
        with self.assertRaisesRegex(SchemaError, "newer"):
            LifeDatabase(self.path)
        with closing(connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0], 99)

    def test_ledger_checksum_and_structure_drift_fail_closed(self):
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            conn.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", ("0" * 64,))
        with self.assertRaisesRegex(SchemaError, "checksum"):
            LifeDatabase(self.path)
        with closing(connect(self.path)) as conn:
            conn.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", (migrations()[0].checksum,))
            conn.execute("DROP INDEX one_active_primary")
        with self.assertRaisesRegex(SchemaError, "drift"):
            LifeDatabase(self.path)

    def test_missing_immutability_trigger_is_detected_before_any_repository_write(self):
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            conn.execute("DROP TRIGGER event_facts_immutable")
        with self.assertRaisesRegex(SchemaError, "drift"):
            LifeDatabase(self.path)

    def test_T07_initial_migration_failure_rolls_back_every_DDL_and_ledger_row(self):
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
            bad = Migration(1, "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT, applied_at_ms INTEGER);\n"
                               "CREATE TABLE staged(id INTEGER);\nINSERT INTO missing_table VALUES(1);\n")
            with self.assertRaises(sqlite3.OperationalError):
                migrate(conn, self.clock, (bad,))
            self.assertEqual(list(conn.execute("SELECT name FROM sqlite_master")), [])
            self.assertFalse(conn.in_transaction)

    def test_T07_failed_upgrade_preserves_previous_schema_data_and_history(self):
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            conn.execute("INSERT INTO migration_runs VALUES('run','synthetic','hash','discovered',1,NULL,'{}')")
            steps = (*migrations(), Migration(len(migrations())+1, "CREATE TABLE partial_upgrade(id INTEGER);\nINSERT INTO absent_table VALUES(1);\n"))
            with self.assertRaises(sqlite3.OperationalError):
                migrate(conn, self.clock, steps)
            self.assertEqual(validate_schema(conn), len(migrations()))
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], len(migrations()))
            self.assertEqual(conn.execute("SELECT count(*) FROM migration_runs").fetchone()[0], 1)
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='partial_upgrade'").fetchone())

    def test_legacy_or_nonempty_unknown_database_is_never_treated_as_new(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
            conn.execute("INSERT INTO metadata VALUES('schema_version','1')")
            conn.commit()
        with self.assertRaisesRegex(SchemaError, "legacy"):
            LifeDatabase(self.path)
        with self.assertRaises(FileExistsError):
            LifeDatabase.create(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM metadata").fetchone()[0], "1")
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone())

    def test_missing_relative_or_uninitialized_database_not_implicitly_created(self):
        with self.assertRaises(ValueError):
            LifeDatabase.create(Path("relative/maivita.db"))
        with self.assertRaises(sqlite3.OperationalError):
            LifeDatabase(self.path)
        self.assertFalse(self.path.exists())
        self.path.touch()
        with self.assertRaisesRegex(SchemaError, "migration required"):
            LifeDatabase(self.path)
        self.assertEqual(self.path.stat().st_size, 0)

    def test_noncontiguous_migration_sequence_and_empty_ledger_rejected(self):
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
            with self.assertRaisesRegex(SchemaError, "contiguous"):
                migrate(conn, self.clock, (Migration(2, "SELECT 1;\n"),))
        LifeDatabase.create(self.path, clock=self.clock)
        with closing(connect(self.path)) as conn:
            conn.execute("DELETE FROM schema_migrations")
        with self.assertRaisesRegex(SchemaError, "no migration history"):
            LifeDatabase(self.path)

    def test_original_legacy_entry_license_schema_and_host_fixtures_preserved(self):
        baseline = json.loads((ROOT / "tests/fixtures/preserved-files.json").read_text(encoding="utf-8"))
        for name, expected in baseline.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)
        self.assertFalse((ROOT / "maivita.db").exists())
        self.assertFalse((ROOT / "data").exists())
