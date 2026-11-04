"""Life, date and UI invariants; platform transport is substituted in this file."""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.domain.segment import SchedulerConfig
from maibot_autonomous_planning_plugin.maivita.life.life_state.projections import LifeQueries
from maibot_autonomous_planning_plugin.maivita.life.planning.participation import derive
from maibot_autonomous_planning_plugin.maivita.life.planning.availability import AvailabilityConfig
from maibot_autonomous_planning_plugin.maivita.host.calendar import Calendar
from maibot_autonomous_planning_plugin.maivita.host.frequency import LifeFrequency


class LifeTests(EngineFixture,unittest.TestCase):
    def test_day_late_nap_and_interrupted_sleep(self):
        self.apply('advance',{'initialize_neutral':True});self.segment()
        for _ in range(16):
            self.clock.advance(timedelta(hours=1));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,100)
        self.apply('start_sleep',{'duration_minutes':480},source='authorized')
        for _ in range(8):
            self.clock.advance(timedelta(hours=1));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,20)
        self.apply('wake',source='authorized')
        self.segment()
        for _ in range(20):
            self.clock.advance(timedelta(hours=1));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,100)
        self.apply('start_sleep',{'duration_minutes':120},source='authorized')
        self.clock.advance(timedelta(hours=1));self.apply('advance',{})
        self.clock.advance(timedelta(minutes=30));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,85)
        self.apply('interrupt_sleep',{'minutes':5},source='authorized')
        self.clock.advance(timedelta(minutes=5));self.apply('advance',{})
        self.assertAlmostEqual(self.engine.state().sleepiness,85.42,places=2)

    def segment(self,**extra):
        self.engine.scheduler=SchedulerConfig(enabled=True)
        return self.apply('advance_segment',{'event':'阅读','category':'rest','location':'家','emotion':'平静',
            'minutes':60,'continue':False,'sleep':False,**extra})

    def test_sleepiness_independent_of_energy_and_rest(self):
        self.apply('advance',{'initialize_neutral':True})
        self.segment()
        self.clock.advance(timedelta(hours=1));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,55)
        self.assertEqual(self.engine.state().energy,53)
        self.apply('start_sleep',{'duration_minutes':480},source='authorized')
        self.clock.advance(timedelta(hours=1));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,45)

    def test_long_unobserved_interval_is_bounded(self):
        self.apply('advance',{'initialize_neutral':True});self.segment()
        self.clock.advance(timedelta(days=4));self.apply('advance',{})
        self.assertEqual(self.engine.state().sleepiness,55)

    def test_future_details_not_saved_then_observed_reflection_is_immutable(self):
        reflection={'details':['泡了一杯茶'],'next_step':'继续读下去'}
        self.segment(reflection=reflection)
        q=LifeQueries(self.engine)
        self.assertEqual(q.get_public_life_context()['continuing'],[])
        self.clock.advance(timedelta(minutes=16));self.segment(reflection=reflection,**{'continue':True})
        self.assertEqual(q.get_public_life_context()['activity']['reflection']['details'],['泡了一杯茶'])
        self.clock.advance(timedelta(minutes=16));self.segment(reflection={'details':['另一件事']},**{'continue':True})
        self.assertEqual(q.get_public_life_context()['activity']['reflection']['details'],['泡了一杯茶'])

    def test_private_reflection_does_not_escape_or_transfer_mood(self):
        self.apply('start_activity',{'name':'私事','category':'work','visibility':'restricted'})
        self.clock.advance(timedelta(minutes=16));self.segment(reflection={'details':['私事细节'],'next_step':'私事后续'},emotion='')
        facts=LifeQueries(self.engine).get_public_life_context()
        self.assertNotIn('私事',str(facts));self.assertEqual(facts['activity']['emotion'],'')

    def test_continuing_cap_expiry_and_cross_day(self):
        self.segment()
        for i in range(7):
            self.clock.advance(timedelta(minutes=16));self.segment(event=str(i),reflection={'details':[str(i)],'next_step':'明天继续'})
        q=LifeQueries(self.engine)
        self.assertEqual(len(q.get_public_life_context()['continuing']),5)
        self.clock.advance(timedelta(days=1));self.assertEqual(len(q.get_public_life_context()['continuing']),5)
        self.clock.advance(timedelta(days=7));self.assertEqual(q.get_public_life_context()['continuing'],[])
        with self.db.read_view() as u:
            self.assertEqual(u.connection.execute("SELECT count(*) FROM activity_details WHERE status='archived'").fetchone()[0],2)

    def test_participation_threshold_hysteresis_and_sleep(self):
        state=replace(self.engine.state(),energy=70,fatigue=20,sleepiness=65)
        cfg=AvailabilityConfig()
        self.assertEqual(derive(state,None,cfg,self.clock.now_utc())['multiplier'],.6)
        state=replace(state,sleepiness=60)
        self.assertEqual(derive(state,None,cfg,self.clock.now_utc(),.6)['multiplier'],.6)
        self.assertEqual(derive(state,None,cfg,self.clock.now_utc(),1)['multiplier'],1)
        self.segment(category='study')
        with self.db.read_view() as u:a=u.activities.get(self.engine.state().primary_activity_id)
        self.assertFalse(derive(state,a,cfg,self.clock.now_utc())['proactive_allowed'])


class CalendarTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_keeps_local_date_and_recovers_after_short_cache(self):
        import asyncio
        cancelled=asyncio.Event()
        async def stalled(*args,**kwargs):
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        api=AsyncMock(side_effect=stalled)
        c=Calendar(SimpleNamespace(api=SimpleNamespace(call=api)))
        now=datetime(2026,11,4,tzinfo=timezone.utc)
        result=await c.get(now,'Asia/Shanghai')
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result['date'],'2026-11-04');self.assertIsNone(result['statutory'])
        api.side_effect=None
        api.return_value={'date':'2026-11-04','statutory':{'on_holiday':True,'is_makeup_workday':False,'available':True}}
        result=await c.get(now+timedelta(minutes=6),'Asia/Shanghai')
        self.assertTrue(result['statutory']['on_holiday'])

    async def test_day_timezone_and_failed_retry(self):
        api=AsyncMock(return_value={'error':'not installed'})
        c=Calendar(SimpleNamespace(api=SimpleNamespace(call=api)))
        now=datetime(2026,12,31,15,59,tzinfo=timezone.utc)
        a=await c.get(now,'Asia/Shanghai');self.assertEqual(a['date'],'2026-12-31');self.assertIsNone(a['statutory'])
        await c.get(now+timedelta(seconds=10),'Asia/Shanghai');self.assertEqual(api.await_count,1)
        await c.get(now+timedelta(minutes=2),'Asia/Shanghai');self.assertEqual(api.await_count,2)
        await c.get(now+timedelta(minutes=3),'UTC');self.assertEqual(api.await_count,3)

    async def test_leap_day_wrong_date_and_unreliable_negative_flags(self):
        now=datetime(2028,2,29,tzinfo=timezone.utc)
        api=AsyncMock(return_value={'date':'2028-02-28'})
        c=Calendar(SimpleNamespace(api=SimpleNamespace(call=api)))
        self.assertIsNone((await c.get(now,'UTC'))['statutory'])
        api.return_value={'date':'2028-02-29','statutory':{'on_holiday':False,'is_makeup_workday':False,'available':True}}
        c=Calendar(c.ctx);self.assertIsNone((await c.get(now,'UTC'))['statutory'])
        api.return_value['statutory']['is_makeup_workday']=True
        c=Calendar(c.ctx);self.assertTrue((await c.get(now,'UTC'))['statutory']['is_makeup_workday'])


class FrequencyTests(EngineFixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.values={'group':.8,'private':.9}
        async def get(stream):return self.values[stream]
        async def put(stream,value):self.values[stream]=value;return {'success':True}
        ctx=SimpleNamespace(frequency=SimpleNamespace(get_adjust=AsyncMock(side_effect=get),set_adjust=AsyncMock(side_effect=put)),
            chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=[{'session_id':k,'stream_id':k,'is_group_session':k=='group'} for k in self.values])))
        query=LifeQueries(self.engine)
        self.plugin=SimpleNamespace(ctx=ctx,runtime=SimpleNamespace(database=self.db,engine=self.engine,queries=query),
            active_config=SimpleNamespace(instance=SimpleNamespace(allowed_streams=[]),availability=SimpleNamespace(adjust_group_frequency=True)))
        self.frequency=LifeFrequency(self.plugin)
        self.apply('start_activity',{'name':'学习','category':'study'})

    async def test_single_writer_restore_and_private_unchanged(self):
        await self.frequency.tick();self.assertAlmostEqual(self.values['group'],.24)
        self.assertEqual(self.values['private'],.9)
        await self.frequency.close();self.assertEqual(self.values['group'],.8)

    async def test_external_change_not_overwritten_on_tick_or_unload(self):
        await self.frequency.tick();self.values['group']=.12
        await self.frequency.tick();self.assertEqual(self.frequency.claims()['group']['status'],'conflict')
        await self.frequency.close();self.assertEqual(self.values['group'],.12)

    async def test_restart_recovers_original_not_compounded_multiplier(self):
        await self.frequency.tick();new=LifeFrequency(self.plugin)
        await new.tick();self.assertAlmostEqual(self.values['group'],.24)
        await new.close();self.assertEqual(self.values['group'],.8)

    async def test_disable_restores_and_zero_stays_zero(self):
        await self.frequency.tick();self.plugin.active_config.availability.adjust_group_frequency=False
        await self.frequency.tick();self.assertEqual(self.values['group'],.8)
        self.values['group']=0;self.plugin.active_config.availability.adjust_group_frequency=True
        await self.frequency.tick();self.assertEqual(self.values['group'],0)

    async def test_new_session_scope_change_and_unconfirmed_restore(self):
        await self.frequency.tick()
        self.values['new']=1
        self.plugin.ctx.chat.get_all_streams.return_value.append({'session_id':'new','stream_id':'new','is_group_session':True})
        await self.frequency.tick();self.assertEqual(self.values['new'],.3)
        self.plugin.active_config.instance.allowed_streams=['new']
        await self.frequency.tick();self.assertEqual(self.values['group'],.8)
        original=self.plugin.ctx.frequency.set_adjust.side_effect
        self.plugin.ctx.frequency.set_adjust.side_effect=TimeoutError
        await self.frequency.close();self.assertEqual(self.frequency.claims()['new']['status'],'restoring')
        self.plugin.ctx.frequency.set_adjust.side_effect=original
        fresh=LifeFrequency(self.plugin)
        self.plugin.active_config.availability.adjust_group_frequency=False
        await fresh.tick();self.assertEqual(self.values['new'],1);self.assertEqual(fresh.claims(),{})
