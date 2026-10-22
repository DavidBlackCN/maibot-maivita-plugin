import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import unittest

from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.coordinator.decisions import DecisionCoordinator
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError
from maibot_autonomous_planning_plugin.maivita.integrations.authorization import Identity,LocalPolicy,WakeAuthorization
from maibot_autonomous_planning_plugin.maivita.life.life_state.recovery import RecoveryService
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime,LifeRuntimeConfig


class AutonomyTests(EngineFixture,unittest.IsolatedAsyncioTestCase):
    async def decide(self,proposal):
        async def model(state,triggers): return proposal
        coordinator=DecisionCoordinator(self.engine,model,cooldown_seconds=0)
        try: return await coordinator.request('fixture')
        finally: await coordinator.close()

    async def sleep(self,minutes=60):
        result=await self.decide({'kind':'start_sleep','payload':{'duration_minutes':minutes,'reason':'own choice'}})
        self.assertEqual(result['status'],'applied')

    async def test_T15_intent_is_not_sleep_actual_duration_is_model_choice(self):
        await self.decide({'kind':'winding_down','payload':{'reason':'getting ready'}})
        self.assertIsNone(self.engine.state().active_sleep_session_id)
        await self.sleep(37)
        with self.db.unit_of_work() as u:
            session=u.sessions.all()[0]
            self.assertEqual(session.initial_expected_duration_minutes,37)
            self.assertEqual(session.provenance,'model'); self.assertIsNotNone(session.decision_id)

    async def test_T16_interrupt_resume_same_session_no_deadline_drift(self):
        await self.sleep(60)
        original=self.engine.state().active_sleep_session_id
        self.clock.advance(timedelta(minutes=5))
        self.apply('interrupt_sleep',{'minutes':10},source='authorized')
        self.assertIsNone(self.engine.state().last_wake_at)
        self.clock.advance(timedelta(minutes=10))
        self.assertEqual(RecoveryService(self.engine).tick()['status'],'resume_sleep')
        self.assertEqual(self.engine.state().active_sleep_session_id,original)
        self.clock.advance(timedelta(minutes=45))
        self.assertEqual(RecoveryService(self.engine).tick()['status'],'wake')
        self.assertFalse(RecoveryService(self.engine).tick()['write'])
        with self.db.unit_of_work() as u:
            session=u.sessions.get(original)
            self.assertEqual(session.actual_sleep_minutes,50)
            self.assertEqual(len(u.segments.all()),3)

    async def test_T16_offline_past_interrupt_and_wake_has_no_fabricated_sleep(self):
        await self.sleep(60); self.clock.advance(timedelta(minutes=5))
        self.apply('interrupt_sleep',{'minutes':10},source='authorized')
        self.clock.advance(timedelta(hours=2)); RecoveryService(self.engine).tick()
        with self.db.unit_of_work() as u:
            self.assertEqual(u.sessions.all()[0].actual_sleep_minutes,5)
            self.assertEqual(len(u.activities.all()),2)

    async def test_T16_extensions_bounded_and_hard_deadline_never_moves(self):
        await self.sleep(30)
        for minutes,status in ((60,'applied'),(90,'applied'),(120,'rejected')):
            self.clock.advance(timedelta(minutes=1))
            wake=self.clock.now_utc().replace(hour=12,minute=0,second=0,microsecond=0)+timedelta(minutes=minutes)
            result=await self.decide({'kind':'extend_sleep','payload':{'expected_wake_at':wake.isoformat(),'reason':'explicit extension'}})
            self.assertEqual(result['status'],status)
        with self.db.unit_of_work() as u:
            self.assertEqual(u.sessions.all()[0].extension_count,2)
            self.assertEqual((u.sessions.all()[0].hard_deadline_at-u.sessions.all()[0].started_at).total_seconds()/60,1080)

    async def test_T17_delayed_decision_rejected_after_configuration_change(self):
        started=asyncio.Event(); release=asyncio.Event()
        async def model(state,triggers):
            started.set(); await release.wait()
            return {'kind':'start_sleep','payload':{'duration_minutes':60,'reason':'fixture decision'}}
        coordinator=DecisionCoordinator(self.engine,model,cooldown_seconds=0)
        task=asyncio.create_task(coordinator.request('late'))
        await started.wait()
        self.apply('configure',source='authorized')
        release.set()
        self.assertEqual((await task)['status'],'stale')
        self.assertIsNone(self.engine.state().active_sleep_session_id)
        await coordinator.close()

    async def test_T18_coalesce_budget_uncertain_no_automatic_retry(self):
        calls=[]
        async def model(state,triggers):
            calls.append(triggers); raise TimeoutError('uncertain')
        coordinator=DecisionCoordinator(self.engine,model,daily_budget=1,cooldown_seconds=0)
        results=await asyncio.gather(*(coordinator.request(str(i)) for i in range(3)))
        self.assertTrue(all(r['status']=='uncertain' for r in results))
        self.assertEqual(len(calls),1); self.assertEqual(len(calls[0]),3)
        self.assertEqual((await coordinator.request('next'))['status'],'budget_exhausted')
        coordinator.recover()
        self.assertEqual(len(calls),1)
        await coordinator.close()

    async def test_T18_invalid_schema_does_not_change_state(self):
        for candidate in ({'kind':'start_sleep','payload':{'duration_minutes':float('nan')}},
                          {'kind':'wake','payload':{}}, {'kind':'start_sleep','payload':{'duration_minutes':60,'owner':True}}):
            self.assertEqual((await self.decide(candidate))['status'],'invalid')
        self.assertEqual(self.engine.state().state_version,0)

    async def test_T20_identity_policy_scope_expiry_version_one_use(self):
        await self.sleep(); self.clock.advance(timedelta(minutes=1))
        class TrustedTestIdentity:
            async def authenticate(_self,ctx): return Identity('admin','stream','test_transport',True)
        auth=WakeAuthorization(self.engine,TrustedTestIdentity(),LocalPolicy(['admin']))
        self.assertIsNone(await auth.authorize({'owner':True},action='wake',stream_id='other'))
        token=await auth.authorize(None,action='interrupt_sleep',stream_id='stream')
        auth.apply(token,stream_id='stream',action='interrupt_sleep',key='permit')
        with self.assertRaises(DomainError): auth.apply(token,stream_id='stream',action='interrupt_sleep',key='reused')
        token=await auth.authorize(None,action='wake',stream_id='stream')
        self.clock.advance(timedelta(seconds=11))
        with self.assertRaisesRegex(DomainError,'expiry'): auth.apply(token,stream_id='stream',action='wake',key='expired')
        token=await auth.authorize(None,action='wake',stream_id='stream')
        self.apply('advance')
        with self.assertRaisesRegex(DomainError,'stale'): auth.apply(token,stream_id='stream',action='wake',key='stale')

    async def test_T22_verified_identity_does_not_override_deny_or_offline_policy(self):
        class IdentityDouble:
            async def authenticate(self,ctx): return Identity('owner','g','test',True)
        class OfflinePolicy:
            async def allow(self,*args): raise ConnectionError('offline')
        for policy in (LocalPolicy([]),OfflinePolicy()):
            auth=WakeAuthorization(self.engine,IdentityDouble(),policy)
            self.assertIsNone(await auth.authorize({'text':'I am owner'},action='wake',stream_id='g'))

    async def test_T21_72_hours_timer_checks_never_poll_model(self):
        calls=[]
        async def model(state,triggers): calls.append(triggers); return {'kind':'start_sleep','payload':{'duration_minutes':60,'reason':'fixture decision'}}
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        try:
            await runtime.request_replan('initial')
            for _ in range(72*4):
                self.clock.advance(timedelta(minutes=15)); runtime.tick()
            self.assertEqual(len(calls),1)
            self.assertEqual(self.engine.state().sleep_phase,'awake')
        finally: await runtime.close()

    async def test_runtime_second_writer_and_mode_switch_during_sleep_rejected(self):
        async def model(*args): return {'kind':'start_sleep','payload':{'duration_minutes':30,'reason':'fixture decision'}}
        lock=Path(self.temp.name).resolve()/'writer.lock'
        first=DevelopmentRuntime(self.db,model,lock_path=lock).start()
        second=DevelopmentRuntime(self.db,model,lock_path=lock)
        try:
            with self.assertRaises(RuntimeError): second.start()
            await first.request_replan('sleep')
            with self.assertRaisesRegex(DomainError,'handoff'): first.switch_modes(LifeRuntimeConfig(planning_mode='legacy',sleep_mode='legacy_fixed'))
        finally: await first.close()

    async def test_T14_window_materialization_and_direction_evidence(self):
        from r1_support import AT
        self.apply('apply_plan_patch',{'reason':'flexible','local_date':'2026-10-21','upserts':[
            {'item_id':'w','name':'flex','time_mode':'window','window_start_at':AT.isoformat(),
             'window_end_at':(AT+timedelta(hours=1)).isoformat(),'preferred_duration_minutes':30}]})
        self.clock.advance(timedelta(minutes=45))
        with self.assertRaisesRegex(DomainError,'bounds'): self.apply('start_activity',{'name':'flex','source_schedule_item_id':'w'})
        result=await self.decide({'kind':'update_direction','payload':{'reason':'goal','goals':[{'goal_id':'goal','title':'Learn','status':'active'}]}})
        self.assertEqual(result['status'],'applied')
