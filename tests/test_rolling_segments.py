"""Stage two: real SQLite transitions and deterministic 24-hour RPC simulations."""
import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

import r0_support
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import ManualClock, to_epoch_ms
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError, LifeState
from maibot_autonomous_planning_plugin.maivita.life.domain.segment import SchedulerConfig
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime, LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase, connect
from maibot_autonomous_planning_plugin.maivita.life.storage.schema import migrate, migrations, validate_schema
from maibot_autonomous_planning_plugin.maivita.life.storage.repositories import SPECS
from maibot_autonomous_planning_plugin.maivita.host.model_port import parse_candidate, SEGMENT_CONTRACT
from maibot_autonomous_planning_plugin.maivita.host.model_port import SDKModelPort
from maibot_autonomous_planning_plugin.maivita.host.config import ModelSettings
from maibot_autonomous_planning_plugin.maivita.host.command_status import snapshot, render_status

MODULE='maibot_autonomous_planning_plugin.maivita.life.planning.segments'


def payload(**changes):
    return {'event':'读小说','location':'家里','emotion':'放松','minutes':45,
            'continue':False,'energy_rate':-0.08, **changes}


class SegmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        # Midnight in Asia/Shanghai, 96 windows constitute one local day.
        self.clock=ManualClock(datetime(2026,10,28,16,tzinfo=timezone.utc))
        self.db=LifeDatabase.create(self.root/'life.db',clock=self.clock)
        self.calls=[]
        async def model(state,triggers,*,task_kind='present'):
            self.calls.append((self.clock.now_utc(),task_kind))
            if task_kind=='planning':
                return {'kind':'apply_plan_patch','payload':{'local_date':self.clock.to_local(self.clock.now_utc(),'Asia/Shanghai').date().isoformat(), 'upserts':[], 'reason':'test day'}}
            if task_kind=='present':
                return {'kind':'winding_down','payload':{}}
            return payload(minutes=15)
        self.model=model
        self.cfg=LifeRuntimeConfig(scheduler=SchedulerConfig(enabled=True))
        self.runtime=DevelopmentRuntime(self.db,model,config=self.cfg,lock_path=self.root/'lock').start()
        self.engine=self.runtime.engine

    async def asyncTearDown(self):
        await self.runtime.close()
        self.tmp.cleanup()

    def apply(self, **changes):
        return self.engine.execute(Command(idempotency_key=str(self.engine.state().state_version),
            kind='advance_segment',expected_state_version=self.engine.state().state_version,payload=payload(**changes)))

    def current(self):
        with self.db.read_view() as u:
            return u.activities.get(self.engine.state().primary_activity_id)

    def deadline(self):
        with self.db.read_view() as u:
            return u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=?',
                                       (self.engine.state().primary_activity_id,)).fetchone()[0]

    def body(self, **values):
        with self.db.unit_of_work() as u:
            state=u.states.get('default')
            u.states.update(replace(state,state_version=state.state_version+1,**values),expected_version=state.state_version)
            u.commit()

    async def test_continue_switch_and_resume_keep_single_authority(self):
        first=self.apply()['primary_activity_id']; end=self.deadline()
        self.clock.advance(timedelta(minutes=15))
        self.apply(**{'continue':True,'event':'','location':'','emotion':''})
        self.assertEqual(self.current().activity_id,first)
        self.assertIsNone(self.current().ended_at)
        self.assertEqual(self.current().location,'家里')
        self.assertEqual(self.deadline(),end+45*60000)
        self.apply(event='休息',energy_rate=0.1)
        with self.db.read_view() as u:
            old=u.activities.get(first)
            self.assertEqual(old.status,'interrupted')
            self.assertEqual(old.ended_at,self.clock.now_utc())
        self.clock.advance(timedelta(minutes=15))
        with self.assertRaisesRegex(DomainError, 'retained interrupted'):
            self.apply(resumes_activity_id=first)
        with self.db.read_view() as u:
            self.assertEqual(sum(a.status=='active' for a in u.activities.all()),1)

    async def test_four_trigger_paths_and_initial_empty(self):
        self.assertEqual(await self.runtime.segments._should_decide(),(True,'no_current_segment'))
        self.apply()
        self.assertEqual(await self.runtime.segments._should_decide(),(False,'continue'))
        for values in ({'fatigue':80,'energy':50},{'fatigue':10,'energy':20}):
            self.body(**values)
            self.assertEqual(await self.runtime.segments._should_decide(),(True,'body_threshold'))
        self.body(fatigue=0,energy=100)
        self.clock.advance(timedelta(minutes=45))
        self.assertEqual(await self.runtime.segments._should_decide(),(True,'segment_due'))

    async def test_probability_miss_extends_without_rpc_and_window_survives_restart(self):
        self.apply(); before=self.deadline()
        result=await self.runtime.segments.tick()
        self.assertEqual(result['status'],'continuing')
        self.assertEqual(self.deadline(),before)
        self.assertEqual(self.calls,[])
        await self.runtime.close()
        self.runtime=DevelopmentRuntime(self.db,self.model,config=self.cfg,lock_path=self.root/'lock').start()
        self.engine=self.runtime.engine
        self.assertEqual((await self.runtime.segments.tick())['status'],'not_due')

    async def test_extreme_body_readings_reject_demanding_activity_atomically(self):
        self.apply(); first=self.current()
        self.clock.advance(timedelta(minutes=15))
        for values in ({'fatigue':95,'energy':50},{'fatigue':0,'energy':5}):
            self.body(**values)
            with self.assertRaisesRegex(DomainError,'forbids'):
                self.apply(event='出门跑步',category='travel')
            self.assertEqual(self.current(),first)
        self.apply(event='休息',energy_rate=0.1)
        self.assertEqual(self.current().name,'休息')

    async def test_duration_and_payload_guards(self):
        for values in ({'minutes':14},{'minutes':181},{'minutes':True},{'continue':'false'},
                       {'energy_rate':float('nan')},{'energy_rate':-1},{'location':None}):
            with self.subTest(values=values), self.assertRaises(DomainError): self.apply(**values)
        with self.assertRaisesRegex(DomainError,'no segment'): self.apply(**{'continue':True})
        with self.assertRaisesRegex(DomainError,'bounds'): self.apply(event='睡眠',minutes=721,energy_rate=0.1)
        self.apply(event='睡眠',minutes=720,energy_rate=0.1)
        self.assertEqual(self.engine.state().sleep_phase,'sleeping')

    async def test_sleep_continuation_is_bounded_and_recovers_to_awake(self):
        self.apply(event='睡眠',minutes=60,energy_rate=0.1)
        session_id=self.engine.state().active_sleep_session_id
        self.clock.advance(timedelta(minutes=15))
        self.apply(**{'continue':True,'minutes':720})
        with self.db.read_view() as u:
            session=u.sessions.get(session_id)
            self.assertEqual(session.expected_wake_at,session.hard_deadline_at)
            self.assertEqual(self.deadline(),to_epoch_ms(session.hard_deadline_at))
        self.clock.advance(timedelta(minutes=705))
        self.runtime.tick()
        self.assertEqual(self.engine.state().sleep_phase,'awake')
        with self.db.read_view() as u:
            self.assertEqual(u.sessions.get(session_id).actual_sleep_minutes,720)

    async def test_sleep_change_closes_session_and_actual_sleep(self):
        self.apply(event='睡眠',minutes=60,energy_rate=0.1)
        self.clock.advance(timedelta(minutes=15))
        with self.assertRaisesRegex(DomainError,'sleep changes'):
            self.apply(event='吃早餐',category='daily_life')
        self.engine.execute(Command(kind='wake',source='authorized',payload={},idempotency_key='trusted-wake',expected_state_version=self.engine.state().state_version))
        self.assertEqual(self.engine.state().sleep_phase,'awake')
        self.assertIsNone(self.engine.state().active_sleep_session_id)
        with self.db.read_view() as u:
            self.assertEqual(u.sessions.all()[0].actual_sleep_minutes,15)

    async def test_energy_rate_is_integrated_once_and_status_has_expected_end(self):
        self.body(energy=50,fatigue=20)
        self.apply(); self.clock.advance(timedelta(minutes=15))
        await self.runtime.segments.tick()
        self.assertAlmostEqual(self.engine.state().energy,49.75)
        await self.runtime.segments.tick()
        self.assertAlmostEqual(self.engine.state().energy,49.75)
        text=render_status(snapshot(self.runtime,'Asia/Shanghai',None))
        for expected in ('读小说','家里','已进行 15 分钟','预计还剩 30 分钟','0/20'):
            self.assertIn(expected,text)
        context=self.runtime.segments.context()
        self.assertEqual(context['current']['elapsed_minutes'],15)
        self.assertEqual(context['body']['energy'],49.75)

    async def test_24_hours_share_budget_across_planning_present_segments_and_restart(self):
        # Planning, immediate request and all 96 due windows compete for 20.
        await self.runtime.request_replan('daily-planning:2026-10-29')
        self.clock.advance(timedelta(minutes=1))
        await self.runtime.request_replan('explicit-present')
        self.clock.advance(timedelta(minutes=1))
        for index in range(96):
            if index==48:
                await self.runtime.close()
                self.runtime=DevelopmentRuntime(self.db,self.model,config=self.cfg,lock_path=self.root/'lock').start()
                self.engine=self.runtime.engine
            self.runtime.tick()
            await self.runtime.process_triggers()
            self.clock.advance(timedelta(minutes=15))
        self.assertEqual(len(self.calls),20)
        self.assertEqual([kind for _,kind in self.calls[:2]],['planning','segment'])
        with self.db.read_view() as u:
            self.assertEqual(u.connection.execute('SELECT count(*) FROM decision_runs').fetchone()[0],20)
            self.assertEqual(u.connection.execute("SELECT count(*) FROM decision_runs WHERE status='applied'").fetchone()[0],20)
        self.assertIsNotNone(self.current())
        # New local day receives a fresh budget.
        self.runtime.tick(); await self.runtime.process_triggers()
        self.assertEqual(len(self.calls),21)

    async def test_failed_calls_and_retries_use_shared_budget(self):
        calls=[]
        async def invalid(*args,**kwargs):
            calls.append(1); return {'bad':'output'}
        self.runtime.coordinator.model=invalid
        self.runtime.coordinator.daily_budget=3
        await self.runtime.segments.tick()
        self.clock.advance(timedelta(minutes=15))
        await self.runtime.segments.tick()
        self.clock.advance(timedelta(minutes=15))
        result=await self.runtime.segments.tick()
        self.assertEqual(result['reason'],'budget_exhausted')
        self.assertEqual(len(calls),3)
        with self.db.read_view() as u:
            self.assertEqual(u.connection.execute('SELECT count(*) FROM decision_runs').fetchone()[0],3)

    async def test_concurrent_ticks_only_claim_one_window(self):
        result=await asyncio.gather(*(self.runtime.segments.tick() for _ in range(5)))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(sum(r['status']=='not_due' for r in result),4)

    async def test_rpc_unknown_outcome_is_counted_without_retry(self):
        model=AsyncMock(side_effect=ConnectionError('unknown outcome'))
        self.runtime.coordinator.model=model
        result=await self.runtime.segments.tick()
        self.assertEqual(result['reason'],'uncertain')
        self.assertEqual(model.await_count,1)
        with self.db.read_view() as u:
            self.assertEqual(u.connection.execute('SELECT status FROM decision_runs').fetchone()[0],'uncertain')

    async def test_sdk_segment_contract_persona_and_context_reach_model(self):
        ctx=SimpleNamespace(llm=SimpleNamespace(generate=AsyncMock(return_value={
            'success':True,'response':json.dumps(payload())})),
            config=SimpleNamespace(get=AsyncMock(return_value='')))
        self.runtime.coordinator.model=SDKModelPort(ctx,ModelSettings(life_context='喜欢独处和读书'),
            context=lambda: {'now':self.clock.now_utc(), 'character_timezone':'Asia/Shanghai',
                            'segment':self.runtime.segments.context(), 'plan':self.runtime.queries.get_plan()})
        result=await self.runtime.segments.tick()
        self.assertEqual(result['status'],'applied')
        prompt=json.loads(ctx.llm.generate.call_args.args[0])
        self.assertEqual(prompt['task_kind'],'segment')
        self.assertEqual(prompt['contract'],SEGMENT_CONTRACT)
        self.assertIn('喜欢独处和读书',prompt['character_context'])
        self.assertIn('body',prompt['context']['segment'])
        self.assertIn('plan',prompt['context'])
        self.assertEqual(self.current().location,'家里')

    async def test_stale_model_cannot_replace_new_observation(self):
        started, release=asyncio.Event(),asyncio.Event()
        async def slow(*args,**kwargs):
            started.set(); await release.wait()
            return {'kind':'advance_segment','payload':payload()}
        self.runtime.coordinator.model=slow
        pending=asyncio.create_task(self.runtime.segments.tick())
        await started.wait()
        current=self.apply(event='等快递')['primary_activity_id']
        release.set()
        self.assertEqual((await pending)['status'],'stale')
        self.assertEqual(self.current().activity_id,current)

    async def test_zero_energy_rate_does_not_use_legacy_category_rate(self):
        self.body(energy=50.5)
        self.apply(energy_rate=0)
        self.clock.advance(timedelta(minutes=15))
        await self.runtime.segments.tick()
        self.assertEqual(self.engine.state().energy,50.25)

    async def test_scheduler_disabled_does_not_invoke_segments(self):
        self.runtime.switch_modes(replace(self.cfg,scheduler=SchedulerConfig()))
        for _ in range(4):
            self.runtime.tick(); await self.runtime.process_triggers()
            self.clock.advance(timedelta(minutes=15))
        self.assertEqual(self.calls,[])


class SegmentMigrationTests(unittest.TestCase):
    def test_schema_4_to_5_preserves_records_defaults_and_constraints(self):
        clock=ManualClock(datetime(2026,10,29,tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'old.db'
            path.touch()
            with closing(connect(path)) as conn:
                migrate(conn,clock,migrations()[:4])
                # Existing rows must remain readable without a table rebuild.
                spec=SPECS['states']
                state=LifeState(updated_at=clock.now_utc(), last_advanced_at=clock.now_utc(), primary_activity_id='old-activity')
                conn.execute('BEGIN')
                conn.execute(f"INSERT INTO life_state ({','.join(spec.columns)}) VALUES ({','.join('?' for _ in spec.columns)})",spec.encode(state))
                conn.execute("INSERT INTO activities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ('old-activity','default','旧活动',to_epoch_ms(clock.now_utc()),'model','rest','primary','',
                     None,'active',None,None,None,None,None,'public_summary'))
                conn.commit()
                conn.execute("INSERT INTO migration_runs VALUES('old','test','hash','discovered',1,NULL,'{}')")
                sql=(Path(__file__).resolve().parents[1]/'maivita/life/storage/migrations/005_activity_context.sql').read_text()
                self.assertNotIn('CREATE TABLE',sql)
                self.assertEqual(migrate(conn,clock,migrations()[:5]),5)
                self.assertEqual(validate_schema(conn,migrations()[:5]),5)
                self.assertEqual(conn.execute('SELECT count(*) FROM migration_runs').fetchone()[0],1)
                columns={r[1]:r for r in conn.execute('PRAGMA table_info(activities)')}
                for field,default in [('location',"''"),('emotion',"''"),('energy_rate','0.0')]:
                    self.assertEqual(columns[field][4],default)
                self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='life_segments'").fetchone())
                self.assertEqual(migrate(conn,clock,migrations()[:5]),5)
                self.assertEqual(migrate(conn,clock),8)
            database=LifeDatabase(path,clock=clock)
            with database.read_view() as u:
                old=u.activities.get('old-activity')
                self.assertEqual((old.name,old.location,old.emotion,old.energy_rate),('旧活动','','',0.0))
                self.assertIsNone(old.ended_at)

    def test_segment_json_contract_is_separate_and_strict(self):
        candidate=parse_candidate(json.dumps(payload()),task_kind='segment')
        self.assertEqual(candidate['kind'],'advance_segment')
        self.assertIn('这一小段',SEGMENT_CONTRACT['instruction'])
        for text in ('{"continue":false,"continue":true}', '{"energy_rate":NaN}', '[]'):
            with self.assertRaises(DomainError): parse_candidate(text,task_kind='segment')

    def test_invalid_scheduler_configuration(self):
        for values in ({'decision_interval_minutes':0},
                       {'segment_min_minutes':181},
                       {'segment_sleep_max_minutes':1440}):
            with self.subTest(values=values), self.assertRaises(DomainError): SchedulerConfig(**values)
