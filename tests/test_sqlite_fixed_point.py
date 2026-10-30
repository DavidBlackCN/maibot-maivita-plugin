"""Regression coverage for the system SQLite 3.37 STRICT/REAL/trigger bug."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest

from r1_support import AT, activity, clock, sleeping, state
from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import to_epoch_ms
from maibot_autonomous_planning_plugin.maivita.life.domain.models import Habit, Plan, ScheduleItem
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase, connect
from maibot_autonomous_planning_plugin.maivita.life.storage.repositories import SPECS
from maibot_autonomous_planning_plugin.maivita.life.storage.schema import (
    Migration, migrate, migrations, schema_signature, validate_schema,
)


class FixedPointMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'schema5.db'
        self.path.touch()
        self.clock = clock()

    def seed(self, conn):
        migrate(conn, self.clock, migrations()[:5])
        snapshot, actual, session, segment = sleeping()
        plan = Plan(plan_id='p', character_id='default', local_date=AT.date(), source='fixture')
        item = ScheduleItem(item_id='i', character_id='default', plan_id='p', name='window',
                            source='fixture', time_mode='window', window_start_at=AT,
                            window_end_at=AT+timedelta(hours=1), preferred_duration_minutes=12.5)
        # Preserve closed observations and a live circular state/activity/session graph.
        closed = replace(segment, segment_id='closed', ended_at=segment.started_at+timedelta(seconds=90))
        segment = replace(segment, started_at=closed.ended_at)
        actual = replace(actual, started_at=segment.started_at, source_schedule_item_id='i',
                         location='bedroom', emotion='calm', energy_rate=-0.0505)
        session = replace(session, initial_expected_duration_minutes=510.5, actual_sleep_minutes=1.5)
        snapshot = replace(snapshot, energy=1.005, fatigue=12.345, stress=100,
                           sleep_debt_minutes=20.5, sleep_history_coverage_days=1)
        records = [('states', snapshot), ('states', state(character_id='unknown')),
                   ('plans', plan), ('items', item), ('sessions', session),
                   ('segments', closed), ('segments', segment), ('activities', actual),
                   ('habits', Habit(habit_id='h', character_id='default', content='fixture', confidence=0.87654, weight=1.23456))]
        conn.execute('BEGIN')
        for name, record in records:
            # Deliberately use the OLD codec, so migration inputs are genuine REALs.
            spec = replace(SPECS[name], fixed_fields=(), positive_minutes=())
            conn.execute(f"INSERT INTO {spec.table} ({','.join(spec.columns)}) VALUES ({','.join('?' for _ in spec.columns)})",
                         spec.encode(record))
        conn.execute('INSERT INTO activity_checkpoints VALUES(?,?,0)', ('a1', to_epoch_ms(AT)))
        conn.execute('INSERT INTO plan_item_checks VALUES(?,?,1,0)', ('i', to_epoch_ms(AT)))
        conn.commit()

    def test_populated_5_to_6_preserves_graph_nulls_history_and_guards(self):
        with closing(connect(self.path)) as conn:
            self.seed(conn)
            guards = [tuple(row) for row in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('trigger','index') AND tbl_name NOT IN ('body_rule_cutovers','conversation_state') ORDER BY type,name")]
            counts = {row[0]: conn.execute(f'SELECT count(*) FROM {row[0]}').fetchone()[0]
                      for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")}
            old_ledger = [tuple(row) for row in conn.execute('SELECT * FROM schema_migrations')]
            self.assertEqual(migrate(conn, self.clock), 8)
            self.assertEqual(validate_schema(conn), 8)
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM schema_migrations WHERE version<=5')], old_ledger)
            self.assertEqual([tuple(row) for row in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('trigger','index') AND tbl_name NOT IN ('body_rule_cutovers','conversation_state') ORDER BY type,name")], guards)
            for table, count in counts.items():
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], count, table)
            self.assertEqual(tuple(conn.execute("SELECT energy,fatigue,stress,sleep_debt_minutes FROM life_state WHERE character_id='default'").fetchone()), (conn.execute("SELECT CAST(ROUND(ROUND(1.005,2)*100) AS INTEGER)").fetchone()[0],1235,10000,21))
            self.assertEqual(tuple(conn.execute("SELECT energy,fatigue,stress,sleep_debt_minutes FROM life_state WHERE character_id='unknown'").fetchone()), (None,None,None,None))
            self.assertEqual(tuple(conn.execute('SELECT initial_expected_duration_minutes,actual_sleep_minutes FROM sleep_sessions').fetchone()), (511,2))
            self.assertEqual(conn.execute('SELECT energy_rate FROM activities').fetchone()[0], -51)
            self.assertEqual(conn.execute('SELECT preferred_duration_minutes FROM schedule_items').fetchone()[0], 13)
            self.assertEqual(tuple(conn.execute('SELECT confidence,weight FROM habits').fetchone()), (0.87654,1.23456))
            for table in ('life_state','activities','sleep_sessions','schedule_items'):
                columns = list(conn.execute(f'PRAGMA table_info({table})'))
                self.assertNotIn('REAL', [row[2] for row in columns])
                self.assertEqual(conn.execute('SELECT strict FROM pragma_table_list WHERE name=?', (table,)).fetchone()[0], 1)
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            # Re-running must not scale a second time or add a second ledger entry.
            before = '\n'.join(conn.iterdump())
            self.assertEqual(migrate(conn,self.clock), 8)
            self.assertEqual('\n'.join(conn.iterdump()), before)
            migrated_energy=conn.execute("SELECT energy FROM life_state WHERE character_id='default'").fetchone()[0]/100
        db = LifeDatabase(self.path, clock=self.clock)
        with db.unit_of_work() as u:
            old = u.states.get('default')
            self.assertEqual((old.energy,old.fatigue,old.stress,old.sleep_debt_minutes), (migrated_energy,12.35,100,21))
            u.states.update(replace(old,state_version=1,energy=60.25,fatigue=25,stress=0),expected_version=0)
            u.commit()  # Includes aggregate validation of rounded sleep cache.
        with closing(connect(self.path)) as conn:
            for sql, message in (
                ("UPDATE life_state SET state_version=1,energy=7000 WHERE character_id='default'", 'regression'),
                ("UPDATE life_state SET state_version=2,energy=10001 WHERE character_id='default'", 'CHECK'),
                ("UPDATE life_state SET state_version=2,energy=60.25 WHERE character_id='default'", 'INTEGER'),
                ("UPDATE activities SET energy_rate=100", 'immutable'),
                ("UPDATE sleep_sessions SET revision=1,actual_sleep_minutes=3", 'immutable'),
                ("DELETE FROM activities", 'cannot be deleted'),
                ("DELETE FROM sleep_sessions", 'cannot be deleted'),
            ):
                with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError,message):
                    conn.execute(sql)
            # The unchanged REAL habit fields are writable on this same SQLite.
            conn.execute('UPDATE habits SET confidence=0.5,weight=2.25')

    def test_failed_006_rolls_back_rebuild_data_ledger_and_foreign_key_mode(self):
        with closing(connect(self.path)) as conn:
            self.seed(conn)
            before = '\n'.join(conn.iterdump())
            signature = schema_signature(conn)
            broken = Migration(6, migrations()[5].sql + '\nINSERT INTO missing_table VALUES(1);\n')
            with self.assertRaises(sqlite3.OperationalError):
                migrate(conn,self.clock,(*migrations()[:5],broken))
            self.assertEqual(schema_signature(conn), signature)
            self.assertEqual('\n'.join(conn.iterdump()), before)
            self.assertEqual(validate_schema(conn,migrations()[:5]),5)
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertFalse(conn.in_transaction)


class FixedPointRuntimeTests(EngineFixture, unittest.TestCase):
    def test_body_updates_use_integer_storage_and_domain_units_after_reopen(self):
        readings = [(0,100,1.005),(50,12.345,99.999),(60.25,0,100)]
        for version, (energy,fatigue,stress) in enumerate(readings,1):
            with self.db.unit_of_work() as u:
                old = u.states.get('default')
                u.states.update(replace(old,state_version=version,energy=energy,fatigue=fatigue,stress=stress),expected_version=version-1)
                u.commit()
        reopened = LifeDatabase(self.db.path,clock=self.clock)
        with reopened.read_view() as u:
            self.assertEqual(u.states.get('default').energy,60.25)
            self.assertEqual(tuple(u.connection.execute('SELECT energy,fatigue,stress,typeof(energy),typeof(fatigue),typeof(stress) FROM life_state').fetchone()),
                             (6025,0,10000,'integer','integer','integer'))

    def test_short_sleep_pieces_round_total_once_and_keep_exact_timestamps(self):
        self.apply('start_sleep',{'duration_minutes':60.5},source='authorized')
        for _ in range(2):
            self.clock.advance(timedelta(seconds=24))
            self.apply('interrupt_sleep',{'minutes':1},source='authorized')
            self.clock.advance(timedelta(minutes=1))
            self.apply('resume_sleep',source='internal')
        self.clock.advance(timedelta(seconds=24))
        self.apply('wake',source='authorized')
        with self.db.read_view() as u:
            session = u.sessions.all()[0]
            self.assertEqual((session.initial_expected_duration_minutes,session.actual_sleep_minutes),(61,1))
            segments = [s for s in u.segments.all() if s.kind=='sleep']
            self.assertEqual(sum((s.ended_at-s.started_at).total_seconds() for s in segments),72)
            self.assertEqual(session.status,'closed')

    def test_fractional_rate_roundtrip_and_closing_original_activity(self):
        actual = activity(energy_rate=-0.0505)
        with self.db.unit_of_work() as u:
            u.activities.add(actual)
            old = u.states.get('default')
            u.states.update(replace(old,state_version=1,primary_activity_id=actual.activity_id),expected_version=0)
            u.commit()
        with self.db.unit_of_work() as u:
            self.assertEqual(u.activities.get('a1').energy_rate,-0.051)
            u.activities.update(replace(actual,status='completed',ended_at=AT,end_reason='done'))
            old = u.states.get('default')
            u.states.update(replace(old,state_version=2,primary_activity_id=None),expected_version=1)
            u.commit()

    def test_codec_matches_migration_rounding_boundaries(self):
        with closing(connect(self.db.path)) as conn:
            for value in (0,0.005,1.005,2.675,12.345,50,99.999,100):
                spec = SPECS['states']
                encoded = dict(zip(spec.columns,spec.encode(state(energy=value))))
                from decimal import Decimal, ROUND_HALF_UP
                expected = int((Decimal(str(value))*100).to_integral_value(rounding=ROUND_HALF_UP))
                self.assertEqual(encoded['energy'],expected)
            for value in (-0.15,-0.0505,0,0.0005,0.1235,0.2):
                spec = SPECS['activities']
                encoded = dict(zip(spec.columns,spec.encode(activity(energy_rate=value))))
                expected = int((Decimal(str(value))*1000).to_integral_value(rounding=ROUND_HALF_UP))
                self.assertEqual(encoded['energy_rate'],expected)
