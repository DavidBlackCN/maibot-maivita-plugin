from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from r1_support import ROOT, AT, activity, clock, sleeping, state
from maibot_autonomous_planning_plugin.maivita.life.domain.models import (
    Commitment, DomainError, LifeEvent, LifeGoal, Plan, Preference, ScheduleItem, ScheduleRevision,
)
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase
from maibot_autonomous_planning_plugin.maivita.life.storage.repositories import RepositoryError
from maibot_autonomous_planning_plugin.maivita.life.storage.schema import SchemaError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name).resolve() / "maivita.db"
        self.clock = clock()
        self.db = LifeDatabase.create(self.path, clock=self.clock, busy_timeout_ms=50)
        with self.db.unit_of_work() as uow:
            uow.states.add(state())
            uow.commit()

    def tearDown(self):
        self.temp.cleanup()

    def event(self, **changes):
        base = LifeEvent(event_id="e", character_id="default", event_type="life.activity.started", state_version=0,
                         event_index=0, occurred_at=AT, recorded_at=AT, payload={"source": ["fixture"]}, source="test")
        return replace(base, **changes)

    def test_T07_explicit_commit_and_default_rollback_no_use_after_finish(self):
        with self.db.unit_of_work() as uow:
            uow.goals.add(LifeGoal(goal_id="rolled-back", character_id="default", title="No commit", provenance="test"))
        with self.db.unit_of_work() as uow:
            self.assertIsNone(uow.goals.get("rolled-back"))
            uow.commit()
            with self.assertRaises(RuntimeError):
                uow.states.get("default")
        with self.assertRaises(RuntimeError):
            with uow:
                pass

    def test_T07_record_roundtrips_keep_plans_facts_events_separate(self):
        plan = Plan(plan_id="p", character_id="default", local_date=date(2026, 10, 21), source="fixture")
        item = ScheduleItem(item_id="i", character_id="default", plan_id="p", name="Planned sleep", source="fixture",
                            time_mode="fixed", start_at=AT, end_at=AT + timedelta(hours=8))
        promise = Commitment(commitment_id="c", character_id="default", title="Future promise", source_stream_id="private-1",
                             provenance="fixture", local_date=date(2099, 1, 1), status="confirmed", confirmation_basis="fixture")
        goal = LifeGoal(goal_id="g", character_id="default", title="Long-term", provenance="fixture", progress_evidence=("legacy signal",))
        preference = Preference(preference_id="pref", character_id="default", content="A reference", source="fixture")
        with self.db.unit_of_work() as uow:
            for repo, model in [(uow.plans, plan), (uow.items, item), (uow.commitments, promise),
                                (uow.goals, goal), (uow.preferences, preference), (uow.events, self.event())]:
                repo.add(model)
            uow.commit()
        self.clock.advance(timedelta(days=1))
        reopened = LifeDatabase(self.path, clock=self.clock)
        with reopened.unit_of_work() as uow:
            self.assertEqual(uow.plans.get("p"), plan)
            self.assertEqual(uow.items.get("i"), item)
            self.assertEqual(uow.commitments.get("c"), promise)
            self.assertIsNone(uow.commitments.get("c").start_at)
            self.assertEqual(uow.goals.get("g"), goal)
            self.assertEqual(uow.preferences.get("pref"), preference)
            self.assertEqual(uow.events.get("e"), self.event())
            self.assertEqual(uow.states.get("default"), state())
            self.assertEqual(uow.activities.all(), [])
            self.assertEqual(uow.sessions.all(), [])

    def test_T06_snapshot_activity_session_and_segment_commit_together(self):
        snap, actual, session, segment = sleeping()
        with self.db.unit_of_work() as uow:
            uow.sessions.add(session)
            uow.segments.add(segment)
            uow.activities.add(actual)
            uow.states.update(replace(snap, state_version=1), expected_version=0)
            uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.sessions.get("s1"), session)
            self.assertEqual(uow.segments.get("seg1"), segment)
            self.assertEqual(uow.activities.get("a1"), actual)
            self.assertEqual(uow.states.get("default").sleep_phase, "sleeping")

    def test_T06_database_indexes_refuse_second_primary_open_session_and_segment(self):
        snap, actual, session, segment = sleeping()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.unit_of_work() as uow:
                uow.activities.add(actual)
                uow.activities.add(replace(actual, activity_id="second"))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.unit_of_work() as uow:
                uow.sessions.add(session)
                uow.sessions.add(replace(session, session_id="second"))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.unit_of_work() as uow:
                uow.sessions.add(session)
                uow.segments.add(segment)
                uow.segments.add(replace(segment, segment_id="second", started_at=AT))

    def test_T06_concurrent_writers_cannot_start_two_primary_activities(self):
        start = threading.Event()
        database = LifeDatabase(self.path, clock=self.clock, busy_timeout_ms=3000)
        def worker(index):
            start.wait()
            try:
                with database.unit_of_work() as uow:
                    before = uow.states.get("default")
                    actual = activity(activity_id=f"concurrent-{index}")
                    uow.activities.add(actual)
                    uow.states.update(replace(before, state_version=before.state_version + 1,
                                              primary_activity_id=actual.activity_id), expected_version=before.state_version)
                    uow.commit()
                return "committed"
            except sqlite3.IntegrityError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, i) for i in range(2)]
            start.set()
            results = [f.result(timeout=5) for f in futures]
        self.assertEqual(sorted(results), ["committed", "rejected"])
        with database.unit_of_work() as uow:
            self.assertEqual(len(uow.activities.all()), 1)

    def test_T06_cross_character_foreign_keys_and_aggregate_mismatch_rollback(self):
        with self.assertRaises(SchemaError):
            with self.db.unit_of_work() as uow:
                uow.states.add(state(character_id="other"))
                uow.plans.add(Plan(plan_id="other-plan", character_id="other", source="test", local_date=date(2026, 10, 21)))
                uow.items.add(ScheduleItem(item_id="cross", character_id="default", plan_id="other-plan", name="wrong scope", source="test"))
                uow.commit()
        with self.assertRaises(DomainError):
            with self.db.unit_of_work() as uow:
                uow.activities.add(activity())  # no matching snapshot
                uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.activities.all(), [])
            self.assertIsNone(uow.states.get("other"))

    def test_T07_failure_mid_transaction_rolls_back_state_activity_and_event(self):
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            with self.db.unit_of_work() as uow:
                uow.activities.add(activity())
                uow.states.update(state(primary_activity_id="a1", state_version=1), expected_version=0)
                uow.events.add(self.event(state_version=1))
                raise RuntimeError("injected failure before commit")
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.states.get("default").state_version, 0)
            self.assertEqual(uow.activities.all(), [])
            self.assertEqual(uow.events.all(), [])

    def test_T07_process_exit_before_and_after_commit_recovers_one_atomic_snapshot(self):
        script = '''
import os,sys
from pathlib import Path
from dataclasses import replace
sys.path.insert(0, str(Path(sys.argv[1]) / "tests"))
from r1_support import AT, clock
from maibot_autonomous_planning_plugin.maivita.life.domain.models import LifeEvent
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase
db=LifeDatabase(Path(sys.argv[2]),clock=clock())
with db.unit_of_work() as u:
    old=u.states.get("default")
    u.states.update(replace(old,state_version=1),expected_version=0)
    u.events.add(LifeEvent(event_id="crash-fixture",character_id="default",event_type="life.state.recovered",
                          state_version=1,event_index=0,occurred_at=AT,recorded_at=AT,payload={},source="test"))
    if sys.argv[3] == "after":
        u.commit()
    os._exit(23)
'''
        for mode, expected in [("before", 0), ("after", 1)]:
            with self.subTest(mode=mode):
                path = self.path.parent / mode / "maivita.db"
                database = LifeDatabase.create(path, clock=self.clock)
                with database.unit_of_work() as uow:
                    uow.states.add(state())
                    uow.commit()
                process = subprocess.run([sys.executable, "-c", script, str(ROOT), str(path), mode], capture_output=True, timeout=10)
                self.assertEqual(process.returncode, 23, process.stderr.decode(errors="replace"))
                for _ in range(2):
                    recovered = LifeDatabase(path, clock=self.clock)
                    with recovered.unit_of_work() as uow:
                        self.assertEqual(uow.states.get("default").state_version, expected)
                        self.assertEqual(len(uow.events.all()), expected)

    def test_T07_commit_disk_failure_simulation_rolls_back_all_records(self):
        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("simulated disk full at commit")
        def failing_connect(path, *, busy_timeout_ms):
            conn = sqlite3.connect(path, factory=FailingCommit, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        with patch("maibot_autonomous_planning_plugin.maivita.life.storage.life_database.connect", side_effect=failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk full"):
                with self.db.unit_of_work() as uow:
                    uow.events.add(self.event())
                    uow.states.update(state(state_version=1), expected_version=0)
                    uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.states.get("default").state_version, 0)
            self.assertEqual(uow.events.all(), [])

    def test_T07_caught_repository_failure_still_prevents_partial_commit(self):
        with self.db.unit_of_work() as uow:
            uow.events.add(self.event())
            with self.assertRaises(sqlite3.IntegrityError):
                uow.events.add(self.event())
            with self.assertRaisesRegex(RuntimeError, "failed write"):
                uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.events.all(), [])

    def test_T06_closed_history_overlap_rejected_but_adjacent_intervals_allowed(self):
        first = activity(started_at=AT - timedelta(hours=1), ended_at=AT - timedelta(minutes=30),
                         status="completed", end_reason="fixture")
        second = replace(first, activity_id="a2", started_at=AT - timedelta(minutes=30), ended_at=AT)
        with self.db.unit_of_work() as uow:
            uow.activities.add(first)
            uow.activities.add(second)
            uow.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.unit_of_work() as uow:
                uow.activities.add(replace(second, activity_id="overlap", started_at=AT - timedelta(minutes=45)))

    def test_outbox_metadata_can_change_without_rewriting_event_facts(self):
        with self.db.unit_of_work() as uow:
            uow.events.add(self.event())
            uow.commit()
        with self.db.unit_of_work() as uow:
            uow.connection.execute("UPDATE life_events SET dispatch_status='delivered',attempt_count=1")
            uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.events.get("e"), self.event())

    def test_T08_storage_compare_and_swap_refuses_stale_version_without_command_protocol(self):
        with self.db.unit_of_work() as uow:
            uow.states.update(state(state_version=1), expected_version=0)
            uow.commit()
        with self.assertRaisesRegex(RepositoryError, "stale"):
            with self.db.unit_of_work() as uow:
                uow.states.update(state(state_version=2), expected_version=0)
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.states.get("default").state_version, 1)

    def test_T07_busy_lock_is_bounded_and_owner_remains_usable(self):
        with self.db.unit_of_work() as first:
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                with self.db.unit_of_work():
                    pass
            self.assertEqual(first.states.get("default"), state())
            first.commit()

    def test_event_future_version_is_not_committed_and_outbox_is_only_storage(self):
        with self.assertRaises(SchemaError):
            with self.db.unit_of_work() as uow:
                uow.events.add(self.event(state_version=99))
                uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.events.all(), [])
            uow.events.add(self.event())
            uow.commit()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT dispatch_status,attempt_count FROM life_events").fetchone(), ("pending", 0))

    def test_history_events_and_revisions_cannot_be_rewritten_or_deleted_by_SQL(self):
        revision = ScheduleRevision(revision_id="r", character_id="default", plan_id="p", basis_version=1,
                                    result_version=2, occurred_at=AT, reason_summary="fixture", changes=({"op": "add"},))
        with self.db.unit_of_work() as uow:
            uow.plans.add(Plan(plan_id="p", character_id="default", local_date=date(2026, 10, 21), source="test"))
            uow.revisions.add(revision)
            uow.events.add(self.event())
            uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.revisions.get("r"), revision)
            with self.assertRaises(RepositoryError):
                uow.events.update(self.event())
        for sql in ["DELETE FROM life_events", "UPDATE life_events SET payload_json='{}'",
                    "DELETE FROM schedule_revisions", "UPDATE schedule_revisions SET reason_summary='rewrite'"]:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with self.db.unit_of_work() as uow:
                    uow.connection.execute(sql)

    def test_database_constraints_reject_invalid_time_shape_even_without_models(self):
        with self.db.unit_of_work() as uow:
            uow.plans.add(Plan(plan_id="p", character_id="default", local_date=date(2026, 10, 21), source="test"))
            uow.items.add(ScheduleItem(item_id="i", character_id="default", plan_id="p", name="intent", source="test"))
            uow.commit()
        for sql in ["UPDATE schedule_items SET start_at_ms=1", "UPDATE plans SET start_at_ms=1",
                    "UPDATE schedule_items SET status='done'", "UPDATE life_state SET state_version=-1"]:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with self.db.unit_of_work() as uow:
                    uow.connection.execute(sql)

    def test_closed_activity_history_remains_when_plan_changes(self):
        closed = activity(status="completed", ended_at=AT, end_reason="explicit_fixture_evidence")
        with self.db.unit_of_work() as uow:
            uow.activities.add(closed)
            uow.plans.add(Plan(plan_id="p", character_id="default", local_date=date(2026, 10, 21), source="test"))
            uow.commit()
        with self.db.unit_of_work() as uow:
            uow.plans.update(replace(uow.plans.get("p"), version=2, status="superseded"))
            uow.commit()
        with self.db.unit_of_work() as uow:
            self.assertEqual(uow.activities.get("a1"), closed)
        for sql in ["DELETE FROM activities", "UPDATE activities SET description='new history'"]:
            with self.assertRaises(sqlite3.IntegrityError):
                with self.db.unit_of_work() as uow:
                    uow.connection.execute(sql)

    def test_T02_new_schema_WAL_backup_and_restore_reopen(self):
        with self.db.unit_of_work() as uow:
            uow.events.add(self.event())
            uow.commit()
            snapshot = self.path.parent / "snapshots" / "maivita.db"
            report = self.db.backup(snapshot)
        restored_path = self.path.parent / "restored" / "maivita.db"
        restored_path.parent.mkdir()
        shutil.copy2(snapshot, restored_path)  # self-contained offline snapshot only
        restored = LifeDatabase(restored_path, clock=self.clock)
        with restored.unit_of_work() as uow:
            self.assertEqual(uow.events.get("e"), self.event())
            self.assertEqual(uow.states.get("default"), state())
        self.assertEqual(report["schema_version"], 7)
        self.assertFalse(report["activated"])
        with self.assertRaises(FileExistsError):
            self.db.backup(snapshot)
