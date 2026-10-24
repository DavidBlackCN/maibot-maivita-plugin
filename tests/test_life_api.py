from datetime import timedelta
from pathlib import Path
import unittest

from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError
from maibot_autonomous_planning_plugin.maivita.integrations.life_api import LifeAPI
from maibot_autonomous_planning_plugin.maivita.life.life_state.projections import QueryScope
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime


class LifeAPITests(EngineFixture,unittest.IsolatedAsyncioTestCase):
    async def test_source_less_activity_and_plan_are_hidden_from_public_api(self):
        async def model(*args): self.fail('read must not call model')
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        try:
            command=self.command('start_activity',{'name':'private activity','visibility':'restricted'})
            # Construct a historical command fixture, bypassing only the new
            # creation boundary. Production callers cannot deserialize Command.
            object.__setattr__(command,'payload',{'name':'private activity','visibility':'source_stream'})
            self.engine.execute(command)
            now=self.clock.now_utc()
            command=self.command('apply_plan_patch',{'reason':'test','local_date':'2026-10-21','upserts':[
                {'item_id':'private-plan','name':'private plan','visibility':'restricted',
                 'time_mode':'fixed','start_at':now.isoformat(),'end_at':(now+timedelta(hours=1)).isoformat()}]})
            payload=dict(command.payload)
            payload['upserts']=[dict(payload['upserts'][0],visibility='source_stream')]
            object.__setattr__(command,'payload',payload)
            self.engine.execute(command)
            api=LifeAPI(runtime)
            for args in ({}, {'stream_id':'forged','private_details':True}):
                state=await api.call('get_life_state',args)
                plan=await api.call('get_plan',args)
                legacy=await api.call('get_current_activity',args)
                self.assertIsNone(state['data']['activity'])
                self.assertEqual(plan['data']['items'],[])
                self.assertNotIn('private activity',str(legacy))
                self.assertNotIn('private plan',str(legacy))
            private=LifeAPI(runtime,scope_resolver=lambda ctx:QueryScope(private_details=True))
            self.assertEqual((await private.call('get_life_state'))['data']['activity']['name'],'private activity')
            self.assertEqual(len((await private.call('get_plan'))['data']['items']),1)
        finally: await runtime.close()

    def test_source_visibility_requires_nonempty_matching_origin(self):
        from types import SimpleNamespace
        from maibot_autonomous_planning_plugin.maivita.life.life_state.projections import visible
        for source in (None, '', ' ', 'source'):
            record=SimpleNamespace(visibility='source_stream',source_stream_id=source)
            for target in (None, '', ' ', 'source', 'other'):
                with self.subTest(source=source,target=target):
                    self.assertEqual(visible(record,QueryScope(target)),source=='source' and target=='source')
            self.assertTrue(visible(record,QueryScope(private_details=True)))
        self.assertFalse(visible(SimpleNamespace(visibility='source_stream'),QueryScope()))
        self.assertTrue(visible(SimpleNamespace(visibility='public_summary'),QueryScope()))
        self.assertFalse(visible(SimpleNamespace(visibility='restricted'),QueryScope('source')))

    async def test_legacy_tools_propose_plan_goals_and_explicit_preserved_delete(self):
        from maibot_autonomous_planning_plugin.maivita.compatibility.life_tools import LifeToolAdapter
        async def model(*args): self.fail('mapping must not call LLM')
        async def allow(*args): return True
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        try:
            tools=LifeToolAdapter(LifeAPI(runtime,authorize=allow,scope_resolver=lambda ctx:QueryScope('private',True)))
            result=await tools.call('apply_schedule_v4',{'local_date':'2026-10-21','idempotency_key':'old-plan',
                'expected_state_version':0,'schedule_data':{'items':[{'name':'Planned sleep','time_slot':'23:00','duration_hours':2,'goal_type':'rest'}]}})
            self.assertTrue(result['ok']); self.assertIsNone(self.engine.state().active_sleep_session_id)
            with self.db.read_view() as u:
                item=u.items.all()[0]; self.assertEqual((item.end_at-item.start_at).total_seconds(),7200)
            create=await tools.call('manage_goal_v4',{'action':'create','name':'Learn','idempotency_key':'goal-create','expected_state_version':1})
            goal_id=create['data']['goal_id']
            complete=await tools.call('manage_goal_v4',{'action':'complete','goal_id':goal_id,'idempotency_key':'goal-complete','expected_state_version':2})
            self.assertFalse(complete['ok'])
            delete=await tools.call('manage_goal_v4',{'action':'delete','goal_id':goal_id,'idempotency_key':'goal-delete','expected_state_version':2})
            self.assertEqual(delete['data']['mapped_action'],'cancel_preserving_record')
            with self.db.read_view() as u: self.assertEqual(u.goals.get(goal_id).status,'cancelled')
        finally: await runtime.close()
    async def test_planned_sleep_due_only_enqueues_evaluation_and_worker_unloads(self):
        import asyncio
        calls=[]
        async def model(state,triggers):
            calls.append(triggers)
            return {'kind':'winding_down','payload':{'reason':'not actually asleep'}}
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        try:
            now=self.clock.now_utc()
            self.apply('apply_plan_patch',{'reason':'sleep intention','local_date':'2026-10-21','upserts':[
                {'item_id':'sleep','name':'sleep plan','kind':'sleep','time_mode':'fixed','start_at':now.isoformat(),'end_at':(now+timedelta(hours=8)).isoformat()}]})
            self.assertEqual(runtime.tick()['status'],'plan_check_due')
            self.assertIsNone(self.engine.state().active_sleep_session_id)
            self.assertEqual((await runtime.process_triggers())['status'],'applied')
            self.assertEqual(self.engine.state().sleep_phase,'winding_down')
            worker=asyncio.create_task(runtime.run())
            await asyncio.sleep(0)
            await runtime.close()
            self.assertTrue(worker.done()); self.assertIsNone(runtime.worker_task)
            self.assertEqual(len(calls),1)
        finally:
            if runtime.opened: await runtime.close()

    async def test_awake_restart_marks_unknown_gap_no_new_activity_or_habit_evidence(self):
        async def model(*args): return {'kind':'winding_down','payload':{}}
        self.apply('start_activity',{'name':'Reading','visibility':'public_summary'})
        self.clock.advance(timedelta(hours=2))
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        try:
            view=runtime.queries.get_life_state()
            self.assertTrue(view['activity_continuity_unknown']); self.assertIsNone(view['activity'])
            with self.db.read_view() as u:
                self.assertEqual(len(u.activities.all()),1)
                self.assertEqual(u.connection.execute('SELECT count(*) FROM observation_gaps').fetchone()[0],1)
            self.apply('end_activity'); self.apply('review_habits')
            with self.db.read_view() as u: self.assertEqual(u.habits.all(),[])
        finally: await runtime.close()

    async def test_config_projection_preserves_disabled_and_rejects_old_allow_all(self):
        from maibot_autonomous_planning_plugin.maivita.legacy.config_models import MaiVitaConfig
        from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import LifeRuntimeConfig
        config=MaiVitaConfig()
        self.assertEqual(LifeRuntimeConfig.from_r0(config).sleep_mode,'legacy_fixed')
        config.sleep.mode='disabled'
        self.assertEqual(LifeRuntimeConfig.from_r0(config,autonomous=True).sleep_mode,'disabled')
        config.compatibility.legacy_empty_admin_all=True
        with self.assertRaisesRegex(DomainError,'empty-admin'): LifeRuntimeConfig.from_r0(config,autonomous=True)

    async def test_read_envelope_legacy_shape_and_forged_private_scope(self):
        async def model(*args): self.fail('read must not call model')
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        try:
            self.apply('start_activity',{'name':'secret'})
            api=LifeAPI(runtime)
            view=await api.call('get_life_state',{'private_details':True},trusted_context={'owner':True})
            self.assertTrue(view['ok']); self.assertEqual(view['api_version'],1); self.assertIsNone(view['data']['activity'])
            old=await api.call('get_current_activity')
            self.assertEqual(set(old),{'has_activity','activity','activity_state','next_activities','as_of','timezone','error'})
            self.assertNotIn('api_version',old)
            denied=await api.call('request_replan',{'reason':'ordinary text','owner':True})
            self.assertEqual(denied['error']['code'],'PERMISSION_DENIED')
        finally: await runtime.close()

    async def test_enqueue_receipt_one_model_call_and_checkpoint_due_not_completion(self):
        calls=[]
        async def model(state,triggers):
            calls.append(triggers)
            return {'kind':'start_activity','payload':{'name':'work','category':'work','check_after_minutes':10}}
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name).resolve()/'writer.lock').start()
        async def allow(ctx,method,scope): return ctx=='trusted'
        try:
            api=LifeAPI(runtime,authorize=allow)
            args={'reason':'explicit request','expected_state_version':0,'idempotency_key':'request-1'}
            first=await api.call('request_replan',args,trusted_context='trusted')
            second=await api.call('request_replan',args,trusted_context='trusted')
            self.assertTrue(first['data']['accepted']); self.assertEqual(first['data'],second['data']); self.assertEqual(calls,[])
            self.assertEqual((await runtime.process_triggers())['status'],'applied')
            self.assertEqual(len(calls),1)
            actual=self.engine.state().primary_activity_id
            self.clock.advance(timedelta(minutes=10)); self.assertEqual(runtime.tick()['status'],'activity_check_due')
            self.assertEqual(self.engine.state().primary_activity_id,actual)
            with self.db.read_view() as u: self.assertEqual(u.activities.get(actual).status,'active')
            runtime.tick()
            with self.db.read_view() as u:
                self.assertEqual(u.connection.execute("SELECT count(*) FROM decision_triggers WHERE kind='check_activity'").fetchone()[0],1)
        finally: await runtime.close()
