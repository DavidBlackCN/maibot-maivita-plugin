"""Public continuity and structured sleep against actual SQLite domain code."""
from datetime import timedelta
from types import SimpleNamespace
import json
import unittest

from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import to_epoch_ms
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError
from maibot_autonomous_planning_plugin.maivita.life.domain.segment import SchedulerConfig
from maibot_autonomous_planning_plugin.maivita.life.life_state.projections import LifeQueries
from maibot_autonomous_planning_plugin.maivita.life.life_state.recovery import RecoveryService
from maibot_autonomous_planning_plugin.maivita.life.planning.segments import SegmentService
from maibot_autonomous_planning_plugin.maivita.host.life_context import chat_background
from maibot_autonomous_planning_plugin.maivita.host.model_port import parse_candidate


class LifeContextTests(EngineFixture, unittest.TestCase):
    def segment(self, **kwargs):
        self.engine.scheduler = SchedulerConfig(enabled=True)
        return self.apply('advance_segment', {'event':'读小说','location':'家里的沙发','emotion':'放松',
            'category':'rest','minutes':60,'continue':False, **kwargs})

    def decision_context(self):
        return SegmentService(SimpleNamespace(engine=self.engine, queries=LifeQueries(self.engine)),
                              SchedulerConfig(enabled=True)).context()

    def chat(self, plan=None):
        return chat_background(LifeQueries(self.engine).get_public_life_context(), plan or {}, 'Asia/Shanghai')

    def test_chat_and_decision_keep_previous_day_without_extra_generation(self):
        self.clock.advance(timedelta(hours=3, minutes=50))  # 23:50 local
        self.segment(event='在公园散步', category='travel', location='公园')
        self.clock.advance(timedelta(minutes=20))
        self.segment()
        self.clock.advance(timedelta(minutes=15))
        version = self.engine.state().state_version
        chat, decision = self.chat(), self.decision_context()
        self.assertIn('10-21 23:50', chat)
        for text in ('在公园散步', '读小说', '家里的沙发', '放松'):
            self.assertIn(text, chat)
            self.assertIn(text, str(decision))
        self.assertEqual(decision['current']['elapsed_minutes'], 15)
        self.assertEqual(self.engine.state().state_version, version)
        for internal in ('state_version', 'confidence', 'activity_id', 'daily_budget'):
            self.assertNotIn(internal, chat)

    def test_private_records_and_unknown_spans_are_not_chat_or_decision_facts(self):
        self.apply('start_activity', {'name':'PRIVATE_SENTINEL','category':'study','visibility':'restricted'})
        self.assertNotIn('PRIVATE_SENTINEL', self.chat()+str(self.decision_context()))
        self.clock.advance(timedelta(minutes=1))
        self.segment(event='GAP_SENTINEL')
        start=self.clock.now_utc()
        self.clock.advance(timedelta(days=2))
        with self.db.unit_of_work() as u:
            u.connection.execute('INSERT INTO observation_gaps VALUES(?,?,?,?,?)',
                ('old-gap','default',to_epoch_ms(start),to_epoch_ms(start+timedelta(hours=1)),'offline'))
            u.commit()
        facts=LifeQueries(self.engine).get_public_life_context()
        self.assertTrue(facts['activity']['continuity_unknown'])
        self.assertIsNone(facts['activity']['duration_minutes'])
        self.assertNotIn('GAP_SENTINEL',self.chat()+str(self.decision_context()))
        self.assertIn('观察缺口',self.chat())
        self.assertIsNone(self.decision_context()['current'])

    def test_plans_stay_future_intentions_and_background_is_bounded(self):
        self.segment(event='x'*1000, location='y'*1000, emotion='z'*1000)
        now=self.clock.now_utc()
        plan={'items':[
            {'name':'PAST_SENTINEL','status':'planned','end_at':(now-timedelta(minutes=1)).isoformat()},
            {'name':'去买贴纸','status':'planned','start_at':(now+timedelta(hours=1)).isoformat()},
            {'name':'CANCELLED_SENTINEL','status':'cancelled'}]}
        text=self.chat(plan)
        self.assertIn('参考打算（尚未发生，可以调整）',text)
        self.assertIn('去买贴纸',text)
        self.assertNotIn('PAST_SENTINEL',text)
        self.assertNotIn('CANCELLED_SENTINEL',text)
        self.assertLess(len(text),2000)
        self.assertLess(len(json.dumps(self.decision_context(),ensure_ascii=False)),3000)

    def test_explicit_sleep_name_independent_and_normal_wake_restores(self):
        self.segment(event='躺下小睡',sleep=True,minutes=240)
        state=self.engine.state()
        self.assertEqual(state.sleep_phase,'sleeping')
        self.assertIsNotNone(state.active_sleep_session_id)
        self.clock.advance(timedelta(minutes=30))
        # Old continuation JSON without a sleep field survives SDK parsing.
        parsed=parse_candidate(json.dumps({'event':'','location':'','emotion':'','minutes':60,'continue':True}), task_kind='segment')
        self.apply(parsed['kind'],parsed['payload'])
        self.assertEqual(self.engine.state().active_sleep_session_id,state.active_sleep_session_id)
        self.clock.advance(timedelta(minutes=271))
        RecoveryService(self.engine).tick()
        self.assertEqual(self.engine.state().sleep_phase,'awake')

    def test_legacy_sleep_and_explicit_awake_are_distinct(self):
        self.segment(event='睡眠',sleep=False)
        self.assertEqual(self.engine.state().sleep_phase,'awake')
        with self.assertRaisesRegex(DomainError,'bounds'):
            self.segment(event='睡眠',sleep=False,minutes=240)
        self.clock.advance(timedelta(minutes=1))
        self.segment(event='睡眠',minutes=240)  # old saved candidate
        self.assertEqual(self.engine.state().sleep_phase,'sleeping')
        with self.assertRaisesRegex(DomainError,'cannot change sleep'):
            self.segment(**{'continue':True,'sleep':False})
        with self.assertRaisesRegex(DomainError,'boolean'):
            self.segment(sleep='true')
