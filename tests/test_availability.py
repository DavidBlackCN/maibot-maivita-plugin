"""Scheme B: real local state/SDK adapter, no live model or chat sends."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import r0_support
import test_i1_development as support
from maibot_autonomous_planning_plugin.maivita.host.command_status import render_status, snapshot
from maibot_autonomous_planning_plugin.maivita.host.config import AvailabilitySettings
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.models import Activity, Category, LifeState
from maibot_autonomous_planning_plugin.maivita.life.planning.availability import AvailabilityConfig, derive_availability


NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.config = AvailabilityConfig()
        self.state = LifeState(updated_at=NOW, last_advanced_at=NOW)
        self.activity = Activity(activity_id='a', character_id='default', name='活动',
                                 started_at=NOW, provenance='test')

    def test_all_categories_and_configured_override(self):
        for category in Category:
            with self.subTest(category=category):
                result = derive_availability(self.state, replace(self.activity, category=category), self.config)
                self.assertEqual(result.state, 'slow' if category in ('study','work','travel','creative') else 'free')
        config = replace(self.config, slow_categories=('social',))
        self.assertEqual(derive_availability(self.state, replace(self.activity, category='social'), config).state, 'slow')
        self.assertEqual(derive_availability(self.state, replace(self.activity, category='study'), config).state, 'free')

    def test_energy_rate_boundary_and_unknown_body(self):
        for rate, expected in ((-0.15,'slow'),(-0.05,'slow'),(-0.049,'free'),(0,'free'),(0.2,'free')):
            with self.subTest(rate=rate):
                self.assertEqual(derive_availability(self.state, replace(self.activity, energy_rate=rate), self.config).state, expected)
        self.assertEqual(derive_availability(self.state, None, self.config).state, 'free')

    def test_sleep_session_including_temporary_wake_wins(self):
        for phase in ('sleeping','temporarily_awake'):
            state = replace(self.state, sleep_phase=phase, active_sleep_session_id='sleep',
                            awake_since_at=NOW if phase=='temporarily_awake' else None,
                            fatigue=100, energy=0)
            result = derive_availability(state, self.activity, self.config)
            self.assertEqual((result.state,result.reason), ('busy','睡眠中'))
            state = replace(state, fatigue=None, energy=None)
            self.assertEqual(derive_availability(state, None, replace(self.config,busy_when_sleeping=False)).state, 'free')

    def test_body_thresholds_are_inclusive_and_configurable(self):
        for values, expected in (({'fatigue':89.99,'energy':10.01},'free'),
                                 ({'fatigue':90},'busy'),({'energy':10},'busy'),
                                 ({'energy':0},'busy'),({'fatigue':100},'busy')):
            with self.subTest(values=values):
                self.assertEqual(derive_availability(replace(self.state,**values), None, self.config).state, expected)
        custom = replace(self.config, busy_fatigue_threshold=80, busy_energy_threshold=20)
        for values in ({'fatigue':80},{'energy':20}):
            self.assertEqual(derive_availability(replace(self.state,**values), self.activity, custom).state, 'busy')

    def test_finished_activity_ignored_and_private_reason_redacted(self):
        study = replace(self.activity, category='study')
        result = derive_availability(self.state, study, self.config, activity_visible=False)
        self.assertEqual(result.state, 'slow')
        self.assertNotIn('学习', result.reason)
        finished = replace(study, status='completed', ended_at=NOW+timedelta(minutes=1), end_reason='done')
        self.assertEqual(derive_availability(self.state, finished, self.config).state, 'free')


class ConfigTests(unittest.TestCase):
    def test_defaults_and_entrypoint_conversion_preserve_settings(self):
        config = IntegrationConfig()
        self.assertFalse(config.availability.enabled)
        self.assertFalse(config.availability.followup_after_busy)
        self.assertEqual(config.selected_config().availability.runtime_config(), AvailabilityConfig())
        config = IntegrationConfig.model_validate({'availability':{'enabled':True,
            'slow_categories':['social'], 'busy_when_sleeping':False,
            'busy_fatigue_threshold':75, 'busy_energy_threshold':25}})
        self.assertEqual(config.selected_config().availability, config.availability)
        self.assertEqual(config.selected_config().availability.runtime_config(),
                         AvailabilityConfig(True, ('social',), False, 75, 25))

    def test_invalid_settings_rejected(self):
        for values in ({'enabled':'false'}, {'busy_when_sleeping':'true'},
                       {'busy_fatigue_threshold':101}, {'busy_fatigue_threshold':-1},
                       {'busy_fatigue_threshold':True}, {'busy_energy_threshold':-1},
                       {'busy_energy_threshold':101}, {'busy_energy_threshold':10.5},
                       {'slow_categories':['stduy']}, {'slow_categories':'study'},
                       {'followup_after_busy':True}, {'unknown':True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                AvailabilitySettings.model_validate(values)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.DevelopmentTests.asyncSetUp
    asyncTearDown = support.DevelopmentTests.asyncTearDown

    async def load(self, **availability):
        config = self.plugin.config.model_dump()
        config['availability'] = {'enabled':True, **availability}
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()

    def body(self, **values):
        with self.plugin.runtime.database.unit_of_work() as u:
            state = u.states.get('default')
            u.states.update(replace(state,state_version=state.state_version+1,**values),
                            expected_version=state.state_version)
            u.commit()

    def execute(self, kind, **payload):
        engine = self.plugin.runtime.engine
        state = engine.state()
        return engine.execute(Command(kind=kind, idempotency_key=str(state.state_version),
            expected_state_version=state.state_version, source='legacy_observed', payload=payload))

    async def hook(self, message=None):
        return await asyncio.wait_for(self.plugin.silence_hook(message=message), 1)

    async def test_busy_abort_free_and_slow_continue_without_rpc_or_extra_queries(self):
        await self.load()
        self.body(energy=50, fatigue=20)
        self.assertEqual(await self.hook({'session_id':'test-stream'}), {'action':'continue'})
        self.execute('start_activity', name='学习', category='study', visibility='public_summary', check_after_minutes=30)
        self.assertEqual(self.plugin.runtime.queries.get_availability()['state'], 'slow')
        self.assertEqual(await self.hook({'session_id':'test-stream'}), {'action':'continue'})
        for values in ({'energy':50,'fatigue':90}, {'energy':10,'fatigue':20}):
            self.body(**values)
            calls = list(self.calls)
            database = self.plugin.runtime.database
            with patch.object(database, 'read_view', wraps=database.read_view) as read:
                self.assertEqual(await self.hook({'session_id':'test-stream'}),
                                 {'action':'abort','reason':'maivita_busy'})
                self.assertEqual(read.call_count, 1)
            self.assertEqual(self.calls, calls)
            data = self.plugin.runtime.queries.get_availability()
            self.assertEqual(data['state'], 'busy')
            self.assertFalse(data['can_reply_recommendation'])
        self.body(energy=50, fatigue=20)
        calls = list(self.calls)
        self.assertEqual(await self.hook(), {'action':'continue'})
        await asyncio.sleep(0)
        self.assertEqual(self.calls, calls)  # No recovery followup or replay.

    async def test_disabled_does_not_enforce_body_busy(self):
        await self.load(enabled=False)
        self.body(energy=0, fatigue=100)
        self.assertEqual(await self.hook(), {'action':'continue'})
        data = self.plugin.runtime.queries.get_availability()
        self.assertEqual(data['state'], 'busy')
        self.assertTrue(data['can_reply_recommendation'])
        text = render_status(snapshot(self.plugin.runtime,'Asia/Shanghai','test-stream'))
        self.assertIn('busy（需要休息）', text)
        self.assertIn('忙闲守卫已关闭', text)
        self.assertIn('不回复原因：无', text)

    async def test_scope_unknown_message_object_and_spoofed_owner(self):
        await self.load()
        self.body(fatigue=90)
        for message in (None, {}, SimpleNamespace(session_id='test-stream'),
                        {'session_id':'test-stream','owner':True,'source_plugin_id':'trusted'}):
            self.assertEqual((await self.hook(message))['reason'], 'maivita_busy')
        self.assertEqual(await self.hook({'session_id':'other-stream'}), {'action':'continue'})

    async def test_sleep_guard_unchanged_with_both_new_switches_off(self):
        await self.load(enabled=False, busy_when_sleeping=False)
        self.execute('start_sleep', duration_minutes=5)
        for message in (None, {'session_id':'test-stream'}):
            self.assertEqual(await self.hook(message),
                             {'action':'abort','reason':'maivita_sleeping_no_receipt_exception'})
        self.assertEqual(await self.hook({'session_id':'other-stream'}), {'action':'continue'})
        self.assertFalse(self.plugin.runtime.queries.get_availability()['available'])

    async def test_sleep_guard_precedes_busy_when_enabled(self):
        await self.load()
        self.execute('start_sleep', duration_minutes=5)
        self.body(energy=0, fatigue=100)
        self.assertEqual((await self.hook())['reason'], 'maivita_sleeping_no_receipt_exception')
        data = self.plugin.runtime.queries.get_availability()
        self.assertEqual((data['state'],data['reason']), ('busy','睡眠中'))

    async def test_status_and_api_agree_and_hide_private_activity_details(self):
        await self.load()
        self.execute('start_activity',name='私密研究',category='study',check_after_minutes=30)
        data = self.plugin.runtime.queries.get_availability()
        text = render_status(snapshot(self.plugin.runtime,'Asia/Shanghai','test-stream'))
        self.assertIn('状态：slow（正在专注）', text)
        self.assertIn('不延迟或补发', text)
        self.assertNotIn('私密研究', text)
        self.assertNotIn('正在学习', text)
        self.assertIn(data['reason'], text)
        self.body(fatigue=90)
        text = render_status(snapshot(self.plugin.runtime,'Asia/Shanghai','test-stream'))
        self.assertIn('状态：busy（需要休息）', text)
        self.assertIn('不回复原因：忙碌静默（MaiVita：疲劳过高）', text)

    async def test_failure_guard_still_applies(self):
        await self.load()
        self.plugin.ready=False
        self.plugin.failure_code='RUNTIME_FAILED'
        self.assertEqual(await self.hook(), {'action':'abort','reason':'maivita_runtime_failed'})
        self.assertEqual(await self.hook({'session_id':'other-stream'}), {'action':'continue'})
