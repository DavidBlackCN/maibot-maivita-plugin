from dataclasses import replace
from datetime import timedelta
import unittest

from test_life_engine import EngineFixture
from r1_support import AT
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError,Preference,SleepSegment
from maibot_autonomous_planning_plugin.maivita.life.events.dispatcher import EventDispatcher
from maibot_autonomous_planning_plugin.maivita.integrations.behavior import BehaviorHandoff
from maibot_autonomous_planning_plugin.maivita.integrations.memory import MemoryAdapter
from maibot_autonomous_planning_plugin.maivita.life.life_state.subjective import sleep_debt


class ContinuityTests(EngineFixture,unittest.TestCase):
    def test_T26_multi_day_habits_weekly_limit_decay_preferences_unchanged(self):
        with self.db.unit_of_work() as u:
            u.preferences.add(Preference(preference_id='hard',character_id='default',content='No work at night',source='explicit',kind='hard_constraint'))
            u.commit()
        for _ in range(3):
            self.apply('start_activity',{'name':'reading','category':'study'})
            self.clock.advance(timedelta(hours=1)); self.apply('end_activity')
            self.clock.advance(timedelta(hours=23))
        self.apply('review_habits')
        with self.db.unit_of_work() as u:
            habit=u.habits.all()[0]
            self.assertEqual(habit.status,'active'); self.assertEqual(len(habit.evidence_ids),3)
            self.assertEqual(u.preferences.get('hard').content,'No work at night')
        with self.assertRaisesRegex(DomainError,'frequency'): self.apply('review_habits')
        self.clock.advance(timedelta(days=31)); self.apply('review_habits')
        with self.db.unit_of_work() as u: self.assertEqual(u.habits.all()[0].status,'archived')

    def test_T27_secondary_limit_compatibility_sleep_and_resume_history(self):
        self.apply('start_activity',{'name':'meal','category':'daily_life','description':'half meal remains'})
        original=self.engine.state().primary_activity_id
        self.clock.advance(timedelta(minutes=1))
        self.apply('start_secondary',{'name':'show','category':'entertainment'})
        self.apply('start_secondary',{'name':'music','category':'entertainment'})
        with self.assertRaises(DomainError): self.apply('start_secondary',{'name':'third','category':'entertainment'})
        self.clock.advance(timedelta(minutes=1)); self.apply('start_activity',{'name':'interrupt','category':'work'})
        self.assertEqual(self.engine.state().secondary_activity_ids,())
        self.clock.advance(timedelta(minutes=1)); self.apply('start_activity',{'name':'ignored','resumes_activity_id':original})
        with self.db.unit_of_work() as u:
            current=u.activities.get(self.engine.state().primary_activity_id)
            self.assertEqual(current.description,'half meal remains'); self.assertEqual(current.resumes_activity_id,original)
            self.assertEqual(u.activities.get(original).status,'interrupted')
        self.clock.advance(timedelta(minutes=1))
        self.apply('start_sleep',{'duration_minutes':20},source='legacy_observed')
        with self.assertRaises(DomainError): self.apply('start_secondary',{'name':'chat','category':'social'})

    def test_T28_summary_failure_keeps_facts_scopes_checksum_and_long_summary(self):
        self.apply('start_activity',{'name':'private study'})
        self.clock.advance(timedelta(minutes=30)); self.apply('end_activity')
        with self.db.unit_of_work() as u: source=u.activities.all()[0]
        with self.assertRaisesRegex(DomainError,'visibility'):
            self.apply('summarize',{'source_ids':[source.activity_id],'visibility':'public_summary'})
        with self.db.unit_of_work() as u:
            self.assertEqual(u.summaries.all(),[]); self.assertEqual(u.activities.all()[0],source)
        self.apply('summarize',{'source_ids':[source.activity_id]})
        with self.db.unit_of_work() as u: summary=u.summaries.all()[0]
        self.apply('cleanup',{'summary_id':summary.summary_id})
        with self.db.unit_of_work() as u:
            self.assertEqual(u.activities.all()[0],source)
            self.assertEqual(u.connection.execute('SELECT removed_count FROM retention_audit').fetchone()[0],0)
        self.apply('summarize',{'source_type':'summaries','source_ids':[summary.summary_id]})
        with self.db.unit_of_work() as u: self.assertEqual(len(u.summaries.all()),2)

    def test_T28_injected_summary_transaction_failure_rolls_back_everything(self):
        self.apply('start_activity',{'name':'fact'}); self.clock.advance(timedelta(minutes=1)); self.apply('end_activity')
        with self.db.unit_of_work() as u: source=u.activities.all()[0]
        self.engine.fault=lambda point: (_ for _ in ()).throw(OSError('disk')) if point=='before_commit' else None
        with self.assertRaises(OSError): self.apply('summarize',{'source_ids':[source.activity_id]})
        with self.db.unit_of_work() as u:
            self.assertEqual(u.summaries.all(),[]); self.assertEqual(u.activities.all()[0],source)

    def test_sleep_debt_midnight_split_recompute_and_unknown_gap(self):
        from maibot_autonomous_planning_plugin.maivita.life.domain.clock import UTC
        from datetime import datetime
        start=datetime(2026,10,20,23,tzinfo=UTC); end=start+timedelta(hours=2)
        segment=SleepSegment(segment_id='s',session_id='session',character_id='default',started_at=start,ended_at=end,provenance='fixture')
        now=datetime(2026,10,22,tzinfo=UTC)
        result=sleep_debt([segment],now=now,timezone='UTC',observed_since=start.replace(hour=0),need_minutes=480)
        self.assertEqual(result,(840,2)); self.assertEqual(result,sleep_debt([segment],now=now,timezone='UTC',observed_since=start.replace(hour=0)))
        self.assertEqual(sleep_debt([segment],now=now,timezone='UTC',observed_since=start.replace(hour=0),observed_intervals=[(start,end)]),(None,0))


class IntegrationTests(EngineFixture,unittest.IsolatedAsyncioTestCase):
    async def test_T23_delivery_unknown_ack_duplicate_same_id_and_expiry(self):
        self.apply('start_activity',{'name':'fact'})
        business_effects=set(); attempts=[]
        async def consumer(event):
            attempts.append(event.event_id); business_effects.add(event.event_id)
            return event.event_id
        dispatcher=EventDispatcher(self.db,fault=lambda point: (_ for _ in ()).throw(ConnectionError('ack lost')))
        dispatcher.subscribe('social-double',consumer)
        await dispatcher.dispatch()
        self.clock.advance(timedelta(seconds=3))
        dispatcher=EventDispatcher(self.db,{'social-double':consumer})
        await dispatcher.dispatch()
        self.assertEqual(len(attempts),2); self.assertEqual(len(business_effects),1); self.assertEqual(attempts[0],attempts[1])
        self.clock.advance(timedelta(minutes=1)); self.apply('end_activity')
        self.clock.advance(timedelta(minutes=31)); await dispatcher.dispatch()
        self.assertEqual(len(attempts),2)
        with self.db.unit_of_work() as u:
            self.assertEqual(u.connection.execute("SELECT count(*) FROM event_deliveries WHERE status='expired'").fetchone()[0],1)

    async def test_T23_retry_cap_no_consumer_no_life_block(self):
        self.apply('start_activity',{'name':'fact'})
        dispatcher=EventDispatcher(self.db)
        self.assertEqual(await dispatcher.dispatch(),0)
        async def offline(event): raise ConnectionError('offline')
        dispatcher.subscribe('offline',offline)
        for _ in range(8):
            await dispatcher.dispatch(); self.clock.advance(timedelta(seconds=70))
        with self.db.unit_of_work() as u:
            row=u.connection.execute('SELECT status,attempt_count FROM event_deliveries').fetchone()
            self.assertEqual(tuple(row),('failed',5))
        self.apply('end_activity')
        self.assertIsNone(self.engine.state().primary_activity_id)

    async def test_T24_owned_cleanup_before_handoff_local_external_exclusive(self):
        calls=[]
        class FrequencyDouble:
            available=True
            async def remove_adjust(self,stream): calls.append(stream); return True
        handoff=BehaviorHandoff(self.db,FrequencyDouble(),owned_streams=['own'])
        self.assertTrue(handoff.local_hooks_enabled()); self.assertTrue(await handoff.switch('external'))
        self.assertFalse(handoff.local_hooks_enabled()); self.assertEqual(calls,['own'])
        self.assertTrue(await handoff.switch('local_compat')); self.assertTrue(handoff.local_hooks_enabled())
        class Unsupported:
            async def remove_adjust(self,stream): return False
        handoff=BehaviorHandoff(self.db,Unsupported(),owned_streams=['leftover'])
        self.assertFalse(await handoff.switch('external')); self.assertTrue(handoff.local_hooks_enabled())

    async def test_T28_memory_authorization_and_idempotent_scope(self):
        self.apply('start_activity',{'name':'private'}); self.clock.advance(timedelta(minutes=1)); self.apply('end_activity')
        with self.db.unit_of_work() as u: source=u.activities.all()[0]
        self.apply('summarize',{'source_ids':[source.activity_id]})
        with self.db.unit_of_work() as u: summary=u.summaries.all()[0]
        calls=[]
        class MemoryDouble:
            async def submit_summary(self,**kwargs): calls.append(kwargs); return kwargs['idempotency_key']
        async def authorize(ctx,summary): return ctx=='trusted_private_scope'
        memory=MemoryAdapter(self.db,MemoryDouble(),authorize,consumer_id='memory-double')
        self.assertFalse(await memory.submit(summary.summary_id,{'owner':True}))
        self.assertEqual(calls,[])
        self.assertTrue(await memory.submit(summary.summary_id,'trusted_private_scope'))
        self.assertTrue(await memory.submit(summary.summary_id,'trusted_private_scope'))
        self.assertEqual(len(calls),1); self.assertEqual(calls[0]['summary'].visibility,'restricted')
