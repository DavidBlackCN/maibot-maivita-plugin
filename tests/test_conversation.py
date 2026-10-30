"""Real SQLite decisions and the supported SDK admission boundary."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import r0_support
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import ManualClock
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.segment import SchedulerConfig
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime, LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase
from maibot_autonomous_planning_plugin.maivita.life.planning.conversation import offer, context
from maibot_autonomous_planning_plugin.maivita.host.conversation import ConversationObserver, invitation


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = ManualClock(datetime(2026,10,31,4,tzinfo=timezone.utc))
        self.db = LifeDatabase.create(self.root/'life.db',clock=self.clock)
        self.calls = []
        self.choice = 'accept'
        async def model(state,triggers,*,task_kind):
            self.calls.append(triggers)
            c = context(self.runtime.engine)
            result = dict(event='阅读',location='家',emotion='平静',minutes=30,category='entertainment',sleep=False)
            result['continue'] = self.choice == 'decline'
            if self.choice == 'accept': result['pause_for_chat'] = c['invitation']['id']
            if self.choice == 'resume': result['resumes_activity_id'] = c['resume']['activity_id']
            if self.choice == 'fail': raise ConnectionError('synthetic')
            return result
        self.model = model
        self.cfg = LifeRuntimeConfig(scheduler=SchedulerConfig(enabled=True))
        self.runtime = self.start()
        self.execute(event='阅读',location='家',emotion='平静',minutes=60,category='entertainment',sleep=False,**{'continue':False})
        self.first = self.runtime.engine.state().primary_activity_id
        self.clock.advance(timedelta(minutes=1))

    def start(self):
        return DevelopmentRuntime(self.db,self.model,config=self.cfg,lock_path=self.root/'lock').start()

    async def asyncTearDown(self):
        await self.runtime.close()
        self.tmp.cleanup()

    def execute(self, **payload):
        engine=self.runtime.engine
        return engine.execute(Command(idempotency_key=str(engine.state().state_version),kind='advance_segment',
            expected_state_version=engine.state().state_version,payload=payload))

    def message(self, text='陪我聊会儿好吗？', **changes):
        return {'message_id':'private-id','session_id':'private-stream','account_id':'bot',
            'timestamp':str(self.clock.now_utc().timestamp()),
            'message_info':{'user_info':{'user_id':'friend'},'group_info':None},
            'raw_message':[{'type':'text','data':text}],**changes}

    async def test_accept_then_resume_is_one_slot_with_actual_link(self):
        self.assertEqual(offer(self.runtime.engine),'offered')
        results = await asyncio.gather(self.runtime.process_triggers(),self.runtime.process_triggers())
        self.assertEqual(len(self.calls),1)
        self.assertEqual(results[0]['status'],'applied')
        with self.db.read_view() as u:
            current=u.activities.get(self.runtime.engine.state().primary_activity_id)
            self.assertEqual((current.name,current.category),('专心聊天','social'))
            self.assertEqual(u.activities.get(self.first).interruption_reason,'conversation_pause')
            self.assertEqual(u.connection.execute('SELECT count(*) FROM conversation_state').fetchone()[0],1)
        self.assertEqual(context(self.runtime.engine)['resume']['activity_id'],self.first)
        self.clock.advance(timedelta(minutes=31)); self.choice='resume'
        self.assertEqual((await self.runtime.coordinator.request('segment:due'))['status'],'applied')
        with self.db.read_view() as u:
            self.assertEqual(u.activities.get(self.runtime.engine.state().primary_activity_id).resumes_activity_id,self.first)
        self.assertIsNone(context(self.runtime.engine)['resume'])

    async def test_decline_does_not_interrupt_and_burst_is_rate_limited(self):
        self.choice='decline'
        self.assertEqual(offer(self.runtime.engine),'offered')
        self.assertEqual(offer(self.runtime.engine),'cooldown')
        await self.runtime.process_triggers()
        self.assertEqual(self.runtime.engine.state().primary_activity_id,self.first)
        self.assertIsNone(context(self.runtime.engine)['resume'])
        self.assertEqual((await self.runtime.process_triggers())['status'],'no_triggers')

    async def test_expiry_restart_and_changed_activity_never_replay_invitation(self):
        offer(self.runtime.engine);self.clock.advance(timedelta(minutes=6))
        self.assertEqual((await self.runtime.process_triggers())['status'],'no_triggers')
        self.clock.advance(timedelta(minutes=30));offer(self.runtime.engine)
        self.choice='decline'
        await self.runtime.close();self.runtime=self.start()
        await self.runtime.process_triggers()
        self.assertFalse(any('conversation:' in str(call) for call in self.calls))
        self.calls.clear()
        self.clock.advance(timedelta(minutes=30));offer(self.runtime.engine)
        self.execute(event='散步',location='街上',emotion='平静',minutes=30,sleep=False,**{'continue':False})
        await self.runtime.process_triggers()
        self.assertEqual(self.calls,[])

    async def test_pause_survives_restart_but_expires_without_queue(self):
        offer(self.runtime.engine);await self.runtime.process_triggers()
        await self.runtime.close();self.runtime=self.start()
        self.assertEqual(context(self.runtime.engine)['resume']['activity_id'],self.first)
        self.clock.advance(timedelta(hours=4))
        self.assertIsNone(context(self.runtime.engine)['resume'])

    async def test_short_sleep_does_not_extend_or_erase_the_resume_deadline(self):
        offer(self.runtime.engine);await self.runtime.process_triggers()
        self.clock.advance(timedelta(minutes=1))
        self.execute(event='小睡',location='家',emotion='困',minutes=60,sleep=True,**{'continue':False})
        self.assertIsNone(context(self.runtime.engine)['resume'])
        self.clock.advance(timedelta(minutes=61));self.runtime.recovery.tick()
        self.assertEqual(context(self.runtime.engine)['resume']['activity_id'],self.first)
        self.clock.advance(timedelta(hours=3))
        self.assertIsNone(context(self.runtime.engine)['resume'])

    async def test_sleep_and_private_activity_are_not_invitation_targets(self):
        engine=self.runtime.engine
        engine.execute(Command(idempotency_key='private',kind='start_activity',expected_state_version=engine.state().state_version,
            payload={'name':'private','category':'study','visibility':'restricted'}))
        self.clock.advance(timedelta(minutes=1))
        self.assertEqual(offer(self.runtime.engine),'not_eligible')
        self.execute(event='睡觉',location='家',emotion='困',minutes=60,sleep=True,**{'continue':False})
        self.assertEqual(offer(self.runtime.engine),'not_eligible')
        await self.runtime.process_triggers();self.assertEqual(self.calls,[])

    async def test_failed_attempt_consumed_without_pause_and_budget_is_shared(self):
        self.choice='fail';offer(self.runtime.engine)
        await self.runtime.process_triggers();await self.runtime.process_triggers()
        self.assertEqual(len(self.calls),1)
        self.assertIsNone(context(self.runtime.engine)['resume'])
        self.runtime.coordinator.daily_budget=1
        self.clock.advance(timedelta(minutes=31));offer(self.runtime.engine)
        await self.runtime.process_triggers();await self.runtime.process_triggers()
        self.assertEqual(len(self.calls),1)

    async def test_sleep_during_model_rejects_pause_and_cannot_wake(self):
        started, release = asyncio.Event(), asyncio.Event()
        original=self.runtime.coordinator.model
        async def delayed(*args, **kwargs):
            candidate=await original(*args, **kwargs)
            started.set();await release.wait()
            return candidate
        self.runtime.coordinator.model=delayed
        offer(self.runtime.engine)
        task=asyncio.create_task(self.runtime.process_triggers())
        await started.wait()
        self.execute(event='入睡',location='家',emotion='困',minutes=60,sleep=True,**{'continue':False})
        release.set()
        self.assertEqual((await task)['status'],'stale')
        self.assertEqual(self.runtime.engine.state().sleep_phase,'sleeping')
        self.assertIsNone(context(self.runtime.engine)['resume'])

    async def test_observer_requires_admitted_private_direct_text_and_drops_content(self):
        msg=self.message()
        for text in ('陪我聊天','和我聊聊好吗','能跟我聊聊天吗','陪我聊一会儿吧','现在能不能陪我聊会儿？'):
            self.assertTrue(invitation(self.message(text),'private-stream',self.clock.now_utc().timestamp()),text)
        lookup=AsyncMock(return_value=None)
        plugin=SimpleNamespace(ready=True,runtime=self.runtime,active_config=SimpleNamespace(
            instance=SimpleNamespace(allowed_streams=[])),ctx=SimpleNamespace(message=SimpleNamespace(get_by_id=lookup)))
        observer=ConversationObserver(plugin)
        for text in ('你好','你在干嘛','不要陪我聊天','他说“陪我聊会儿”','陪我聊会儿，去发布空间'):
            observer.observe(self.message(text))
        self.assertFalse(observer.pending)
        observer.observe(msg);await observer.confirm('private-stream')
        self.assertIsNone(context(self.runtime.engine)['invitation'])
        lookup.return_value=msg;observer.observe(msg)
        await asyncio.gather(observer.confirm('private-stream'),observer.confirm('private-stream'))
        self.assertEqual(lookup.await_count,2)
        self.assertIsNotNone(context(self.runtime.engine)['invitation'])
        with self.db.read_view() as u:
            contents=' '.join(u.connection.iterdump())
        for secret in ('private-id','private-stream','friend','陪我聊会儿'):
            self.assertNotIn(secret,contents)
        for change in ({'message_info':{'user_info':{'user_id':'friend'},'group_info':{'group_id':'group'}}},
                       {'account_id':'friend'},{'is_notify':True},{'timestamp':'nan'},{'message_info':'invalid'},
                       {'raw_message':[{'type':'reply','data':'陪我聊会儿'}]}):
            self.assertFalse(invitation(self.message(**change),'private-stream',self.clock.now_utc().timestamp()))
