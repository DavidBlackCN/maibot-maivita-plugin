"""Task 08: hourly estimates, observable anomalies, and host initialization."""
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch

from test_life_engine import EngineFixture
import test_i1_development as development_support
from test_i1_development import settings
from maibot_autonomous_planning_plugin.maivita.host.config import DevelopmentConfig
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError
from maibot_autonomous_planning_plugin.maivita.life.life_state import subjective
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.life.storage.schema import SchemaError


class BodyEstimatesTests(EngineFixture, unittest.TestCase):
    def test_one_hour_rates(self):
        for category, expected in (
            ('sleeping', (58,45,47)), ('study', (47,52,50.8)),
            ('work', (47,52,50.8)), ('travel', (47,52,50.8)),
            ('entertainment', (49,51,49)), ('rest', (53,48,49)),
        ):
            with self.subTest(category=category):
                # Separate characters avoid introducing transition intervals.
                self.engine.initialize(category)
                def apply(kind, payload):
                    state=self.engine.state(category)
                    self.engine.execute(Command(character_id=category,kind=kind,payload=payload,
                        idempotency_key=kind,expected_state_version=state.state_version,source='legacy_observed'))
                apply('advance', {'initialize_neutral':True})
                if category == 'sleeping':
                    apply('start_sleep', {'duration_minutes':480})
                else:
                    apply('start_activity', {'name':category,'category':category})
                self.clock.advance(timedelta(hours=1))
                state=self.engine.state(category)
                self.engine.execute(Command(character_id=category,kind='advance',payload={},idempotency_key='hour',
                    expected_state_version=state.state_version))
                state=self.engine.state(category)
                self.assertEqual((state.energy,state.fatigue,state.stress),expected)
                self.assertEqual(state.provenance,'fixed-categories-v4')

    def test_sql_high_stress_warns_and_recovers_at_normal_rate(self):
        self.apply('advance', {'initialize_neutral':True})
        self.apply('start_activity', {'name':'rest','category':'entertainment'})
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            conn.execute("UPDATE life_state SET stress=10000,state_version=state_version+1 WHERE character_id='default'")
        self.clock.advance(timedelta(hours=1))
        with self.assertLogs(subjective.logger,level='WARNING') as logs:
            self.apply('advance')
        self.assertEqual(self.engine.state().stress,99)
        self.assertIn('field=stress current=100.0 reason=extreme_value',logs.output[0])
        self.assertEqual(len(logs.records),1)

    def test_sql_out_of_range_still_fails_existing_integrity_check_without_repair(self):
        command=self.command('advance')
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            conn.execute('PRAGMA ignore_check_constraints=ON')
            conn.execute("UPDATE life_state SET stress=10001,state_version=state_version+1 WHERE character_id='default'")
        with self.assertRaisesRegex(SchemaError,'integrity check failed'):
            self.engine.execute(command)
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            self.assertEqual(conn.execute('SELECT stress FROM life_state').fetchone()[0],10001)

    def test_advance_invalid_current_logs_without_repairing(self):
        for value in (-1,101,float('nan'),float('inf')):
            with self.subTest(value=value):
                state=self.engine.state()
                # Simulate corruption at the advance boundary, bypassing the
                # frozen domain object's normal constructor checks for this test.
                object.__setattr__(state,'stress',value)
                with self.assertLogs(subjective.logger,level='WARNING'):
                    with self.assertRaises(DomainError):
                        subjective.advance(None,state,self.clock.now_utc(),{})
                self.assertIs(state.stress,value)

    def test_large_positive_and_negative_delta_log_before_existing_bounds(self):
        self.apply('advance', {'initialize_neutral':True})
        self.apply('start_activity', {'name':'test','category':'work'})
        self.clock.advance(timedelta(hours=1))
        state=replace(self.engine.state(),updated_at=self.clock.now_utc())
        with self.db.read_view() as u:
            actual=u.activities.get(state.primary_activity_id)
            for category, rate, expected in (("rest",3,100),("work",-3,0)):
                with self.subTest(rate=rate):
                    # Bad duplicate observations can inflate a bounded rate.
                    with patch.object(u.activities,'for_character',return_value=[replace(actual,category=category)]*20):
                        with self.assertLogs(subjective.logger,level='WARNING') as logs:
                            after=subjective.advance(u,state,self.clock.now_utc(),{})
                    self.assertEqual(after.energy,expected)
                    self.assertIn('reason=abnormal_delta',logs.output[0])
                    self.assertIn('delta='+str(float(rate*20)),logs.output[0])

    def test_unknowns_and_warning_boundaries(self):
        self.apply('advance')
        state=self.engine.state()
        self.assertEqual((state.energy,state.fatigue,state.stress),(None,None,None))
        for field,value in (('stress',95),('fatigue',95),('energy',5),('energy',100),('stress',None)):
            self.assertIsNone(subjective.body_value_warning(field,value))
        for field,value in (('stress',95.01),('fatigue',95.01),('energy',4.99)):
            self.assertEqual(subjective.body_value_warning(field,value),'extreme_value')


class BodyInitializationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=development_support.DevelopmentTests.asyncSetUp
    asyncTearDown=development_support.DevelopmentTests.asyncTearDown

    def initialization_count(self):
        with self.plugin.runtime.database.read_view() as u:
            return u.connection.execute("SELECT count(*) FROM command_receipts WHERE idempotency_key LIKE 'initialize-body-state:%'").fetchone()[0]

    async def test_first_load_defaults_to_neutral_and_restart_preserves_existing_values(self):
        self.assertTrue(LifeRuntimeConfig().initialize_body_state)
        await self.plugin.on_load()
        state=self.plugin.runtime.engine.state()
        self.assertEqual((state.energy,state.fatigue,state.stress),(50,50,50))
        self.assertEqual(state.confidence,'low')
        self.assertEqual(self.initialization_count(),1)
        with self.plugin.runtime.database.unit_of_work() as u:
            u.states.update(replace(state,energy=60,fatigue=20,stress=30,state_version=state.state_version+1),
                            expected_version=state.state_version)
            u.commit()
        await self.plugin.on_unload()
        await self.plugin.on_load()
        state=self.plugin.runtime.engine.state()
        self.assertEqual((state.energy,state.fatigue,state.stress),(60,20,30))
        self.assertEqual(self.initialization_count(),1)

    async def test_disabled_retains_unknown_then_enabling_initializes(self):
        config=settings(self.work)
        config['routine']={'initialize_body_state':False}
        self.plugin.set_plugin_config(config)
        for _ in range(2):
            await self.plugin.on_load()
            state=self.plugin.runtime.engine.state()
            self.assertFalse(self.plugin.runtime.config.initialize_body_state)
            self.assertEqual((state.energy,state.fatigue,state.stress),(None,None,None))
            self.assertEqual(self.initialization_count(),0)
            await self.plugin.on_unload()
        self.plugin.set_plugin_config(settings(self.work))
        await self.plugin.on_load()
        self.assertEqual(self.plugin.runtime.engine.state().energy,50)
        self.assertEqual(self.initialization_count(),1)

    async def test_partial_state_initializes_only_unknown_fields(self):
        config=settings(self.work)
        config['routine']={'initialize_body_state':False}
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()
        state=self.plugin.runtime.engine.state()
        with self.plugin.runtime.database.unit_of_work() as u:
            u.states.update(replace(state,fatigue=20,stress=30,state_version=state.state_version+1),
                            expected_version=state.state_version)
            u.commit()
        await self.plugin.on_unload()
        self.plugin.set_plugin_config(settings(self.work))
        await self.plugin.on_load()
        state=self.plugin.runtime.engine.state()
        self.assertEqual((state.energy,state.fatigue,state.stress),(50,20,30))

    async def test_explicit_status_marks_sql_extreme_without_mutating(self):
        await self.plugin.on_load()
        await self.plugin.command(text='/maivita status',stream_id='test-stream')
        self.assertNotIn('身体数值异常',json.dumps(self.calls[-1],ensure_ascii=False))
        with closing(sqlite3.connect(self.plugin.runtime.database.path)) as conn, conn:
            conn.execute("UPDATE life_state SET stress=10000,state_version=state_version+1 WHERE character_id='default'")
        before=self.plugin.runtime.engine.state()
        await self.plugin.command(text='/maivita status',stream_id='test-stream')
        sent=[p['args'] for _,p in self.calls if p['capability']=='send.text'][-1]
        self.assertIn('⚠ 身体数值异常：压力 100.00',json.dumps(sent,ensure_ascii=False))
        self.assertEqual(self.plugin.runtime.engine.state(),before)

    def test_invalid_initialization_config_is_rejected(self):
        for value in ('false',0,None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    DevelopmentConfig.model_validate({'routine':{'initialize_body_state':value}})
                with self.assertRaises(DomainError):
                    LifeRuntimeConfig(initialize_body_state=value)
