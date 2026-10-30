"""Task 01b: deterministic local DB/SDK doubles; no live provider or chat sends."""
import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import r0_support
from maibot_autonomous_planning_plugin.maivita.host.command_status import hush_reason, render_status, snapshot, status_text
from maibot_autonomous_planning_plugin.maivita.host.config import ModelSettings, RoutineSettings
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
from maibot_autonomous_planning_plugin.maivita.host.model_port import SDKModelPort, PLANNING_CONTRACT, PRESENT_CONTRACT
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import ManualClock, to_epoch_ms
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime, LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.life.planning.hybrid import candidate
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase
import test_i1_development as development_support


def plan(day='2026-10-29'):
    return {'kind':'apply_plan_patch','payload':{'local_date':day,'upserts':[
        {'item_id':'study-'+day,'name':'学习','category':'study','time_mode':'unscheduled','visibility':'public_summary'}],
        'remove_ids':[],'reason':'今天预留学习时间'}}


def hush_response(**fields):
    return {'success':True,'result':{'ok':True,'data':{'stream_id':'test-stream','hard':'NONE',**fields}}}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name).resolve()
        self.clock=ManualClock(datetime(2026,10,28,23,59,tzinfo=timezone.utc))
        self.db=LifeDatabase.create(self.root/'life.db',clock=self.clock)
        self.calls=[]; self.responses=[]
        async def model(state,triggers,*,task_kind):
            self.calls.append((triggers,task_kind))
            response=self.responses.pop(0) if self.responses else plan(self.clock.to_local(self.clock.now_utc(),'Asia/Shanghai').date().isoformat())
            if isinstance(response,BaseException): raise response
            return response
        self.model=model
        self.cfg=LifeRuntimeConfig(daily_planning_enabled=True,daily_budget=16)
        self.runtime=DevelopmentRuntime(self.db,model,config=self.cfg,lock_path=self.root/'lock').start()
        self.runtime.coordinator.cooldown=0

    async def asyncTearDown(self):
        await self.runtime.close(); self.temp.cleanup()

    def runs(self):
        with self.db.read_view() as u:
            return [dict(r) for r in u.connection.execute('SELECT * FROM decision_runs ORDER BY rowid')]

    async def test_before_at_after_daily_time_once_and_next_day(self):
        self.runtime.tick()
        self.assertEqual((await self.runtime.process_triggers())['status'],'no_triggers')
        self.clock.advance(timedelta(minutes=1)); self.runtime.tick()
        self.assertEqual((await self.runtime.process_triggers())['status'],'applied')
        self.assertEqual(self.calls[0][1],'planning')
        self.clock.advance(timedelta(hours=4)); self.runtime.tick(); await self.runtime.process_triggers()
        self.assertEqual(len(self.calls),1)
        self.clock.advance(timedelta(days=1)); self.runtime.tick(); await self.runtime.process_triggers()
        self.assertEqual(len(self.calls),2)

    async def test_late_start_custom_time_timezone_and_restart(self):
        await self.runtime.close()
        self.cfg=replace(self.cfg,timezone='America/New_York',daily_planning_time='09:30')
        self.runtime=DevelopmentRuntime(self.db,self.model,config=self.cfg,lock_path=self.root/'lock').start()
        self.clock.advance(timedelta(hours=13))  # 08:59 New York, already afternoon Shanghai
        self.runtime.tick(); await self.runtime.process_triggers(); self.assertEqual(len(self.calls),0)
        self.clock.advance(timedelta(hours=3))
        self.runtime.tick(); await self.runtime.process_triggers(); self.assertEqual(len(self.calls),1)
        await self.runtime.close()
        self.runtime=DevelopmentRuntime(self.db,self.model,config=self.cfg,lock_path=self.root/'lock').start()
        self.runtime.tick(); await self.runtime.process_triggers(); self.assertEqual(len(self.calls),1)

    async def test_all_present_actions_rejected_in_daily_batch_without_side_effects(self):
        proposals=[{'kind':'start_activity','payload':{'name':'下午上课','category':'study','check_after_minutes':90}},
                   {'kind':'winding_down','payload':{'reason':'准备休息'}},
                   {'kind':'start_sleep','payload':{'duration_minutes':60,'reason':'困了'}},
                   {'kind':'extend_sleep','payload':{'expected_wake_at':'2026-10-29T10:00:00+08:00','reason':'继续休息'}}]
        before=self.runtime.engine.state()
        for index,proposal in enumerate(proposals):
            self.responses=[proposal,proposal]
            with self.db.unit_of_work() as u:
                key='daily-planning:test'+str(index)
                u.connection.execute('INSERT INTO decision_triggers VALUES(?,?,?,?,0)',(key,'default','enqueue_replan',to_epoch_ms(self.clock.now_utc())))
                u.commit()
            result=await self.runtime.coordinator.request('batch:'+key)
            self.assertEqual(result['status'],'invalid'); self.assertTrue(result['degraded'])
        self.assertEqual(self.runtime.engine.state(),before)
        self.assertEqual([r['status'] for r in self.runs()],['invalid']*8)
        with self.db.read_view() as u:
            self.assertEqual(u.connection.execute('SELECT count(*) FROM activities').fetchone()[0],0)
            self.assertEqual(u.connection.execute('SELECT count(*) FROM decision_triggers WHERE consumed=0').fetchone()[0],0)

    async def test_invalid_then_valid_retry_count_and_planning_only(self):
        self.responses=[{'bad':'json envelope'},deepcopy(PLANNING_CONTRACT['example'])]
        result=await self.runtime.request_replan('daily-planning:2026-10-29')
        self.assertEqual(result['status'],'applied'); self.assertEqual(len(self.calls),2)
        self.assertEqual([r['status'] for r in self.runs()],['invalid','applied'])
        self.assertEqual(self.runtime.engine.state().plan_version,1)
        self.assertIsNone(self.runtime.engine.state().primary_activity_id)
        self.assertFalse(snapshot(self.runtime,'Asia/Shanghai','test-stream')['degraded'])

    async def test_two_failures_persist_degraded_after_restart(self):
        self.responses=[{},{}]
        await self.runtime.request_replan('daily-planning:2026-10-29')
        self.assertEqual(len(self.calls),2)
        await self.runtime.close()
        self.runtime=DevelopmentRuntime(self.db,self.model,config=self.cfg,lock_path=self.root/'lock').start()
        self.assertIn('degraded',render_status(snapshot(self.runtime,'Asia/Shanghai','test-stream')))
        self.assertTrue(all(r['status']=='invalid' for r in self.runs()))

    async def test_timeout_and_network_unknown_are_never_retried(self):
        for error in (TimeoutError(),ConnectionError(),OSError()):
            before=len(self.calls); self.responses=[error]
            result=await self.runtime.request_replan('present')
            self.assertEqual(result['status'],'uncertain'); self.assertTrue(result['degraded'])
            self.assertEqual(len(self.calls),before+1)

    async def test_retry_cannot_exceed_budget(self):
        self.runtime.coordinator.daily_budget=1; self.responses=[{},plan()]
        result=await self.runtime.request_replan('daily-planning:2026-10-29')
        self.assertTrue(result['degraded']); self.assertEqual(len(self.calls),1)
        self.assertIn('budget_exhausted',self.runs()[0]['diagnostic'])

    async def test_update_direction_and_present_decisions_still_allowed(self):
        self.responses=[{'kind':'update_direction','payload':{'goals':[],'reason':'今天休息'}}]
        self.assertEqual((await self.runtime.request_replan('daily-planning:2026-10-29'))['status'],'applied')
        self.responses=[{'kind':'start_activity','payload':{'name':'深夜阅读','check_after_minutes':20,
            'reason':'还有一章未读完，晚于偏好入睡'}}]
        self.assertEqual((await self.runtime.request_replan('activity-check'))['status'],'applied')
        self.assertEqual(self.calls[-1][1],'present')

    async def test_model_invalid_during_basis_change_does_not_retry(self):
        async def model(state,triggers,*,task_kind):
            self.runtime.engine.execute(Command(kind='configure',idempotency_key='config-change',
                source='authorized',expected_state_version=state.state_version,payload={}))
            return {}
        self.runtime.coordinator.model=AsyncMock(side_effect=model)
        result=await self.runtime.request_replan('daily-planning:2026-10-29')
        self.assertEqual(result['status'],'stale')
        self.assertEqual(self.runtime.coordinator.model.await_count,1)

    async def test_status_today_counts_date_boundaries_activity_elapsed_and_degraded(self):
        # Previous local day is still the same UTC date: it must not count today.
        with self.db.unit_of_work() as u:
            for key,when in [('yesterday',self.clock.now_utc()-timedelta(hours=8))]:
                u.connection.execute('INSERT INTO decision_runs (decision_id,character_id,basis_state_version,basis_plan_version,basis_config_revision,started_at_ms,trigger_ids_json,status) VALUES (?,?,?,?,?,?,?,?)',
                    (key,'default',0,0,1,to_epoch_ms(when),'["present"]','applied'))
            u.commit()
        self.responses=[{},plan()]; await self.runtime.request_replan('daily-planning:2026-10-29')
        self.responses=[{'kind':'start_activity','payload':{'name':'阅读','category':'study','visibility':'public_summary','check_after_minutes':60}}]
        await self.runtime.request_replan('present'); self.clock.advance(timedelta(minutes=25))
        data=snapshot(self.runtime,'Asia/Shanghai','test-stream')
        self.assertEqual(data['used'],3); self.assertEqual(data['counts'],Counter(planned=1))
        self.assertEqual(data['planning'],'10-29 07:59'); self.assertEqual(data['minutes'],25)
        self.assertEqual(data['activity'],'阅读'); self.assertEqual(data['wake'],'未知')
        # SQL fixtures use real schedule status constraints and completion evidence.
        with self.db.unit_of_work() as u:
            item=u.items.all()[0]
            u.items.add(replace(item,item_id='done',status='done',completion_evidence='observed'))
            u.items.add(replace(item,item_id='started',status='started'))
            u.items.add(replace(item,item_id='cancelled',status='cancelled'))
            u.items.add(replace(item,item_id='hidden',visibility='restricted'))
            u.items.add(replace(item,item_id='yesterday-item',time_mode='fixed',
                start_at=self.clock.now_utc()-timedelta(days=1),end_at=self.clock.now_utc()-timedelta(hours=23)))
            u.commit()
        data=snapshot(self.runtime,'Asia/Shanghai','test-stream')
        self.assertEqual(data['counts'],Counter(planned=1,done=1,started=1))
        self.assertIn('3 项（不作为实际经历或完成证明）',render_status(data))


class ConfigAndPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_clock_validation_all_fields_and_selected_config(self):
        for cls,fields in [(ModelSettings,['daily_planning_time']),
                           (RoutineSettings,['preferred_sleep_time','preferred_wake_time'])]:
            for field in fields:
                for invalid in ('8:00','24:00','23:60','00:0','08:00:00',' 08:00','08:00\n',None,800):
                    with self.subTest(field=field,value=invalid), self.assertRaises(ValueError): cls(**{field:invalid})
                for valid in ('00:00','08:00','23:59'): self.assertEqual(getattr(cls(**{field:valid}),field),valid)
        with self.assertRaises(DomainError): LifeRuntimeConfig(daily_planning_time='25:00')
        cfg=IntegrationConfig(routine={'preferred_sleep_time':'02:30','preferred_wake_time':'10:00'})
        self.assertEqual(cfg.selected_config().routine,cfg.routine)

    async def make_port(self,proposal):
        self.now=datetime(2026,10,28,18,tzinfo=timezone.utc)  # 02:00 local; after preferred bedtime
        self.ctx=SimpleNamespace(llm=SimpleNamespace(generate=AsyncMock(return_value={
            'success':True,'response':json.dumps(proposal)})),config=SimpleNamespace(get=AsyncMock(return_value='')))
        self.state=SimpleNamespace(state_version=0,plan_version=0,config_revision=1)
        return SDKModelPort(self.ctx,ModelSettings(),context=lambda:{'now':self.now,'character_timezone':'Asia/Shanghai'},
                            routine=RoutineSettings(preferred_sleep_time='01:00',preferred_wake_time='08:00'))

    async def test_planning_prompt_complete_contract_routine_and_local_date(self):
        port=await self.make_port(plan())
        await port(self.state,('daily-planning:2026-10-29',),task_kind='planning')
        prompt=json.loads(self.ctx.llm.generate.call_args.args[0])
        self.assertEqual(set(prompt['contract']['commands']),{'apply_plan_patch'})
        self.assertEqual(prompt['contract']['example']['kind'],'apply_plan_patch')
        self.assertEqual(prompt['contract']['example']['payload']['local_date'],'2026-10-29')
        self.assertIn('不要直接开始活动',prompt['contract']['instruction'])
        self.assertEqual(prompt['context']['routine'],{'preferred_sleep_time':'01:00','preferred_wake_time':'08:00'})
        candidate(prompt['contract']['example'])

    async def test_sdk_rejects_daily_activity_and_present_deviation_is_valid(self):
        proposal={'kind':'start_activity','payload':{'name':'继续阅读','check_after_minutes':20,
                    'reason':'故事快结束了，今天晚一点睡'}}
        port=await self.make_port(proposal)
        with self.assertRaises(DomainError): await port(self.state,('daily-planning:2026-10-29',),task_kind='planning')
        self.assertEqual(port.last['status'],'invalid_response')
        self.assertEqual(await port(self.state,('present',),task_kind='present'),proposal)
        self.assertIn('reason',PRESENT_CONTRACT['commands']['start_activity'])


class HushAndRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_hush_duration_missing_field_failure_and_route(self):
        ctx=SimpleNamespace(call_host_method=AsyncMock())
        ctx.call_host_method.return_value=hush_response(hard='HARD_SILENT',hard_remaining_seconds=125)
        self.assertEqual(await hush_reason(ctx,'test-stream'),'硬静音（MaiHush，剩余 2 分 5 秒）')
        ctx.call_host_method.return_value=hush_response(hard='HARD_SILENT',as_of=100,valid_until=105)
        self.assertEqual(await hush_reason(ctx,'test-stream'),'硬静音（MaiHush，剩余时间未知）')
        for response in (hush_response(),hush_response(hard='HARD_SILENT',stream_id='other'),{'success':False},
                         {'success':True,'result':{'ok':False}},None):
            ctx.call_host_method.return_value=response
            self.assertIsNone(await hush_reason(ctx,'test-stream'))
        for error in (TimeoutError(),ConnectionError()):
            ctx.call_host_method.side_effect=error
            self.assertIsNone(await hush_reason(ctx,'test-stream'))
        ctx.call_host_method.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await hush_reason(ctx,'test-stream')

    def test_five_phases_and_silence_combinations(self):
        data={'wake':'10-29 08:00','activity':'阅读','minutes':25,'counts':Counter(done=1,started=1,planned=1),
              'used':2,'budget':16,'planning':'10-29 08:00','degraded':False}
        for phase,label,hush,reasons in [
            ('awake','清醒',None,'无'),
            ('winding_down','准备睡觉','硬静音（MaiHush，剩余 2 分 5 秒）','硬静音'),
            ('sleeping','睡眠中',None,'睡眠静默（MaiVita）'),
            ('temporarily_awake','临时醒来','硬静音（MaiHush，剩余 2 分 5 秒）','睡眠静默（MaiVita）、硬静音'),
            ('waking','正在醒来',None,'无')]:
            text=render_status({**data,'phase':phase},hush)
            self.assertIn('MaiVita：'+label,text); self.assertIn('不回复原因：'+reasons,text)
            self.assertEqual(len(text.splitlines()),6)


class CommandIsolationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=development_support.DevelopmentTests.asyncSetUp
    asyncTearDown=development_support.DevelopmentTests.asyncTearDown

    async def test_details_only_explicit_status_not_hooks_tools_or_other_views(self):
        await self.plugin.on_load()
        await self.plugin.command(text='/maivita status',stream_id='test-stream')
        sent=[p['args'] for _,p in self.calls if p['capability']=='send.text'][-1]
        self.assertIn('模型调用：',json.dumps(sent,ensure_ascii=False))
        hush_calls=sum(p['capability']=='api.call' for _,p in self.calls)
        for method in (self.plugin.replyer_hook,self.plugin.planner_hook):
            result=await method(session_id='test-stream',extra_prompt='existing')
            self.assertNotIn('不回复原因',json.dumps(result,ensure_ascii=False))
            self.assertNotIn('模型调用',json.dumps(result,ensure_ascii=False))
        for method in (self.plugin.status_tool,self.plugin.get_life_state,self.plugin.get_availability):
            self.assertNotIn('不回复原因',json.dumps(await method(),ensure_ascii=False))
        for command in ('/maivita activity','/maivita','/plan'):
            await self.plugin.command(text=command,stream_id='test-stream')
        self.assertEqual(sum(p['capability']=='api.call' for _,p in self.calls),hush_calls)


    async def test_actual_sdk_retries_malformed_json_then_daily_plan(self):
        await self.plugin.on_load()
        responses=[{'success':True,'response':'{bad JSON'},
                   {'success':True,'response':json.dumps(plan())}]
        with patch.object(self.ctx.llm,'generate',side_effect=responses) as generate:
            result=await self.plugin.runtime.request_replan('daily-planning:2026-10-29')
        self.assertEqual(result['status'],'applied'); self.assertEqual(generate.call_count,2)
        for call in generate.call_args_list:
            prompt=json.loads(call.args[0])
            self.assertEqual(prompt['task_kind'],'planning')
            self.assertIn('routine',prompt['context'])
        self.assertIsNone(self.plugin.runtime.engine.state().primary_activity_id)

    async def test_status_failure_and_existing_sleep_send_gate_remain(self):
        await self.plugin.on_load()
        runtime=self.plugin.runtime
        runtime.engine.execute(Command(kind='start_sleep',idempotency_key='sleep',source='authorized',
            expected_state_version=runtime.engine.state().state_version,payload={'duration_minutes':60,'reason':'困了'}))
        with patch.object(self.ctx,'call_host_method',side_effect=ConnectionError()):
            text=await status_text(self.plugin,'test-stream')
        self.assertIn('睡眠静默（MaiVita）',text)
        before=sum(p['capability']=='send.text' for _,p in self.calls)
        await self.plugin.command(text='/maivita status',stream_id='test-stream')
        self.assertEqual(sum(p['capability']=='send.text' for _,p in self.calls),before)


async def actual_status_examples():
    """Read five aggregate-validated database fixtures through the status path."""
    from maibot_autonomous_planning_plugin.maivita.life.domain.models import (
        Activity, LifeState, Plan, ScheduleItem, SleepSegment, SleepSession)
    results={}
    now=datetime(2026,10,29,5,tzinfo=timezone.utc)
    wake=now.replace(hour=0)
    for phase in ('awake','winding_down','sleeping','temporarily_awake','waking'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); db=LifeDatabase.create(root/'status.db',clock=ManualClock(now))
            runtime=DevelopmentRuntime(db,AsyncMock(),config=LifeRuntimeConfig(daily_budget=16),lock_path=root/'lock')
            asleep=phase in ('sleeping','temporarily_awake')
            started=now-timedelta(minutes=5 if phase=='temporarily_awake' else 25)
            with db.unit_of_work() as u:
                u.states.add(LifeState(updated_at=now,last_advanced_at=now,state_version=5,plan_version=1,
                    config_revision=1,sleep_phase=phase,last_wake_at=wake,
                    awake_since_at=None if phase=='sleeping' else started if phase=='temporarily_awake' else wake,
                    primary_activity_id=None if phase=='waking' else 'current',
                    active_sleep_session_id='session' if asleep else None,active_plan_id='today'))
                u.plans.add(Plan(plan_id='today',character_id='default',source='model',status='active',
                                 version=1,local_date=now.date()))
                for status in ('done','started','planned'):
                    u.items.add(ScheduleItem(item_id=status,character_id='default',plan_id='today',name=status,
                        source='model',visibility='public_summary',status=status,
                        completion_evidence='observed' if status=='done' else None))
                if asleep:
                    session_start=now-timedelta(minutes=30)
                    interrupted=phase=='temporarily_awake'
                    u.sessions.add(SleepSession(session_id='session',character_id='default',started_at=session_start,
                        expected_wake_at=now+timedelta(minutes=30),hard_deadline_at=now+timedelta(hours=1),
                        initial_expected_duration_minutes=60,provenance='model',
                        status='interrupted' if interrupted else 'active',
                        interrupt_until=now+timedelta(minutes=5) if interrupted else None,
                        actual_sleep_minutes=25 if interrupted else 0))
                    if interrupted:
                        u.activities.add(Activity(activity_id='prior-sleep',character_id='default',name='睡眠',
                            category='rest',started_at=session_start,ended_at=started,status='interrupted',
                            end_reason='authorized_interrupt',provenance='model',sleep_session_id='session'))
                        u.segments.add(SleepSegment(segment_id='prior',character_id='default',session_id='session',
                            started_at=session_start,ended_at=started,kind='sleep',provenance='model'))
                    u.segments.add(SleepSegment(segment_id='current',character_id='default',session_id='session',
                        started_at=started,kind='awake_interrupt' if interrupted else 'sleep',provenance='model'))
                if phase!='waking':
                    u.activities.add(Activity(activity_id='current',character_id='default',
                        name='睡眠' if phase=='sleeping' else '短暂清醒' if phase=='temporarily_awake' else '阅读',
                        category='rest' if asleep else 'study',started_at=started,provenance='model',
                        sleep_session_id='session' if asleep else None,visibility='public_summary'))
                for index,trigger in enumerate(('daily-planning:2026-10-29','activity-check')):
                    degraded=phase=='waking' and index==1
                    u.connection.execute('INSERT INTO decision_runs (decision_id,character_id,basis_state_version,basis_plan_version,basis_config_revision,started_at_ms,trigger_ids_json,status,diagnostic) VALUES (?,?,?,?,?,?,?,?,?)',
                        (str(index),'default',0,0,1,to_epoch_ms(wake if index==0 else now),json.dumps([trigger]),
                         'invalid' if degraded else 'applied','degraded:DomainError' if degraded else None))
                u.commit()
            ctx=SimpleNamespace(call_host_method=AsyncMock(return_value=hush_response(
                hard='HARD_SILENT' if phase in ('winding_down','temporarily_awake') else 'NONE',
                hard_remaining_seconds=125)))
            if phase=='waking': ctx.call_host_method.side_effect=ConnectionError('Hush unavailable')
            plugin=SimpleNamespace(runtime=runtime,active_config=SimpleNamespace(instance=SimpleNamespace(timezone='Asia/Shanghai')),ctx=ctx)
            results[phase]=await status_text(plugin,'test-stream')
    return results


class StatusDatabaseExamplesTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_actual_database_outputs(self):
        outputs=await actual_status_examples()
        self.assertEqual(len(outputs),5)
        self.assertIn('阅读（已进行 25 分钟）',outputs['awake'])
        self.assertIn('睡眠（已进行 25 分钟）',outputs['sleeping'])
        self.assertIn('短暂清醒（已进行 5 分钟）',outputs['temporarily_awake'])
        self.assertIn('睡眠静默（MaiVita）、硬静音（MaiHush，剩余 2 分 5 秒）',outputs['temporarily_awake'])
        self.assertIn('degraded',outputs['waking']); self.assertIn('不回复原因：无',outputs['waking'])
        for text in outputs.values():
            self.assertIn('3 项（不作为实际经历或完成证明）',text)
            self.assertIn('模型调用：2/16 次 | 上次规划：10-29 08:00',text)
