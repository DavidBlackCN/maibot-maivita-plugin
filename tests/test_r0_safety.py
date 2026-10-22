"""T01/T02/T05, using disposable real SQLite files and OS locks."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from r0_support import ROOT

from maibot_autonomous_planning_plugin.maivita.legacy.database.goal_db import GoalDatabase
from maibot_autonomous_planning_plugin.maivita.life.storage.safety import (
    WriterLease, discover, fingerprint, inspect_database, resolve_data_dir, restore, snapshot,
)
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source" / "goals.db"
        self.db = GoalDatabase(str(self.source), backup_on_init=False)
        self.db.create_goal(goal_id="future", name="Synthetic promise", description="fixture only",
                            goal_type="pending_commitment", priority="high", creator_id="synthetic",
                            chat_id="global", parameters={"commitment_date": "2099-01-01"})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_T01_discovery_never_guesses_or_creates(self):
        self.assertEqual(discover([self.root / "missing"])["status"], "new_install")
        second = self.root / "other.db"
        second.write_bytes(b"not a real DB")
        self.assertEqual(discover([self.source, second])["status"], "ambiguous")
        self.assertIsNone(discover([self.source, second])["selected"])
        self.assertFalse((self.root / "missing").exists())

    def test_T01_sdk_path_and_absolute_fallback(self):
        ctx = NS(paths=NS(data_dir=self.root / "sdk"))
        self.assertEqual(resolve_data_dir(ctx, str(self.root / "fallback")), (self.root / "sdk").resolve())
        self.assertEqual(resolve_data_dir(NS(), str(self.root / "fallback")), (self.root / "fallback").resolve())
        for ctx, value in [(NS(), ""), (NS(), "relative"), (NS(paths=NS(data_dir="relative")), "")]:
            with self.assertRaises(ValueError):
                resolve_data_dir(ctx, value)

    def test_T02_active_WAL_snapshot_restore_includes_committed_rows(self):
        self.assertTrue(Path(str(self.source) + "-wal").exists())
        before = {p.name: fingerprint(p) for p in self.source.parent.iterdir() if p.suffix != "-shm" and not p.name.endswith("-shm")}
        backup = self.root / "backup" / "goals.db"
        report = snapshot(self.source, backup)
        self.assertEqual(report["validation"]["counts"], {"pending_commitment": 1})
        self.assertEqual(before, {p.name: fingerprint(p) for p in self.source.parent.iterdir() if not p.name.endswith("-shm")})
        destination = self.root / "restored" / "goals.db"
        result = restore(backup, destination, report["snapshot_sha256"])
        self.assertEqual(result["validation"]["integrity"], ["ok"])
        with closing(sqlite3.connect(destination)) as conn:
            self.assertEqual(conn.execute("SELECT parameters FROM goals WHERE goal_id='future'").fetchone()[0],
                             '{"commitment_date": "2099-01-01"}')
        self.assertEqual(fingerprint(backup), fingerprint(destination))

    def test_T01_unknown_version_and_structure_are_rejected_without_rewrite(self):
        conn = self.db._get_connection()
        conn.execute("UPDATE metadata SET value='99'")
        conn.commit()
        self.assertFalse(inspect_database(self.source)["valid_legacy_schema"])
        with self.assertRaises(ValueError):
            snapshot(self.source, self.root / "backup" / "x.db")
        self.assertEqual(conn.execute("SELECT value FROM metadata").fetchone()[0], "99")
        conn.execute("UPDATE metadata SET value='1'")
        conn.execute("ALTER TABLE goals ADD COLUMN future_field TEXT")
        conn.commit()
        self.assertFalse(inspect_database(self.source)["valid_legacy_schema"])

    def test_T02_failed_backup_does_not_allow_database_initialization(self):
        with patch("maibot_autonomous_planning_plugin.maivita.life.storage.safety.snapshot", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                GoalDatabase(str(self.source))

    def test_T02_closed_WAL_requires_explicit_offline_and_creates_no_source_files(self):
        with self.assertRaisesRegex(ValueError, "existing WAL"):
            snapshot(self.source, self.root / "offline" / "bad.db", offline=True)
        self.db.close()
        before = {p.name: fingerprint(p) for p in self.source.parent.iterdir()}
        with self.assertRaisesRegex(ValueError, "sidecars absent"):
            inspect_database(self.source)
        target = self.root / "offline" / "goals.db"
        result = snapshot(self.source, target, offline=True)
        self.assertTrue(result["validation"]["valid_legacy_schema"])
        self.assertEqual(before, {p.name: fingerprint(p) for p in self.source.parent.iterdir()})

    def test_T01_no_overwrite_no_inplace_backup_checksum_refusal(self):
        target = self.root / "backup" / "existing.db"
        target.parent.mkdir()
        target.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            snapshot(self.source, target)
        self.assertEqual(target.read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            snapshot(self.source, self.source.parent / "new.db")
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore(target, self.root / "restored" / "x.db", "wrong")

    def test_T01_damaged_database_is_not_recreated(self):
        broken = self.root / "broken.db"
        broken.write_bytes(b"corruption fixture")
        with self.assertRaises(sqlite3.DatabaseError):
            inspect_database(broken)
        self.assertEqual(broken.read_bytes(), b"corruption fixture")

    def test_T02_cli_dry_run_and_restore(self):
        dest = self.root / "dryrun" / "goals.db"
        command = [sys.executable, str(ROOT / "tools" / "r0_migrate.py")]
        result = subprocess.run(command + ["dry-run", "--source", str(self.source), "--output", str(dest)],
                                capture_output=True, text=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "dry_run_prepared")
        self.assertFalse(report["activated"])
        self.assertEqual(report["new_schema_import"], "not_implemented_R1")
        restored = self.root / "cli-restored" / "goals.db"
        subprocess.run(command + ["restore", "--source", str(dest), "--output", str(restored),
                                  "--sha256", report["snapshot_sha256"]], capture_output=True, check=True)
        self.assertEqual(fingerprint(dest), fingerprint(restored))

    def test_T05_single_writer_cross_process_release_and_crash_recovery(self):
        lease = WriterLease(self.root / "writer.lock")
        lease.acquire()
        script = "from maivita.life.storage.safety import WriterLease; from pathlib import Path; import sys,os; x=WriterLease(Path(sys.argv[1])); x.acquire(); os._exit(0)"
        command = [sys.executable, "-c", script, str(lease.path)]
        blocked = subprocess.run(command, cwd=ROOT, capture_output=True)
        self.assertNotEqual(blocked.returncode, 0)
        lease.release()
        subprocess.run(command, cwd=ROOT, capture_output=True, check=True)  # process exits holding lease
        lease.acquire()  # OS released it after process exit
        lease.release()


if __name__ == "__main__":
    unittest.main()
