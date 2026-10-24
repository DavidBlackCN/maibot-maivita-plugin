import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.domain.models import DomainError,LifeGoal
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime
from maibot_autonomous_planning_plugin.maivita.life.events.dispatcher import EventDispatcher
from maibot_autonomous_planning_plugin.maivita.integrations.social import SocialConsumer,public_events
from maibot_autonomous_planning_plugin.maivita.integrations.contracts import SocialEvent
from maibot_autonomous_planning_plugin.maivita.integrations.life_api import LifeAPI
from maibot_autonomous_planning_plugin.maivita.host.plugin import MaiVitaDevelopmentPlugin


class ReviewTests(EngineFixture,unittest.IsolatedAsyncioTestCase):
    def test_new_records_cannot_promise_source_visibility_without_source(self):
        for kind,payload in [('start_activity',{'name':'secret'}),('start_secondary',{'name':'secret'}),
                             ('manage_goal',{'action':'create','goal_id':'g','title':'secret'})]:
            with self.subTest(kind=kind),self.assertRaisesRegex(DomainError,'source_stream'):
                self.command(kind,payload|{'visibility':'source_stream'})
        with self.assertRaisesRegex(DomainError,'source_stream'):
            self.command('apply_plan_patch',{'upserts':[{'visibility':'source_stream'}]})
        self.assertEqual(self.engine.state().state_version,0)

    async def test_historical_records_stay_hidden_in_tools_aliases_and_prompt(self):
        async def model(*args): self.fail('read called model')
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name)/'lock').start()
        plugin=MaiVitaDevelopmentPlugin(); plugin.runtime=runtime; plugin.api=LifeAPI(runtime); plugin.ready=True
        plugin.active_config=SimpleNamespace(instance=SimpleNamespace(allowed_streams=['stream'],timezone='UTC'))
        try:
            historical=self.command('start_activity',{'name':'SOURCE_ONLY_ACTIVITY'})
            object.__setattr__(historical,'payload',{'name':'SOURCE_ONLY_ACTIVITY','visibility':'source_stream'})
            self.engine.execute(historical)
            # Historical rows are loaded normally; no schema or data widening.
            with self.db.unit_of_work() as u:
                u.goals.add(LifeGoal(goal_id='g',character_id='default',title='SOURCE_ONLY_GOAL',provenance='fixture',visibility='source_stream'))
                u.commit()
            for output in (await plugin.status_tool(),await plugin.old_goal_tool(),await plugin.get_current_activity(),
                           await plugin.old_current_activity(),await plugin.replyer_hook(session_id='stream',extra_prompt=''),
                           await plugin.planner_hook(session_id='stream',extra_prompt='')):
                self.assertNotIn('SOURCE_ONLY',str(output))
        finally: await runtime.close()

    async def test_json_ack_binding_and_terminal_rejection(self):
        self.apply('start_activity',{'name':'SECRET'})
        data=public_events(self.db)
        self.assertNotIn('SECRET',str(data))
        parsed=SocialEvent.from_wire(data['events'][0])
        self.assertIsNotNone(parsed.occurred_at.utcoffset())
        async def reject(value):
            return {'contract_version':'1.0.0','event_id':value['event_id'],'consumer_id':'peer','status':'rejected'}
        dispatcher=EventDispatcher(self.db); dispatcher.subscribe('peer',SocialConsumer('peer',reject))
        await dispatcher.dispatch(); await dispatcher.dispatch()
        with self.db.read_view() as u:
            self.assertEqual(tuple(u.connection.execute('SELECT status,attempt_count FROM event_deliveries').fetchone()),('failed',1))
        async def wrong(value): return {'event_id':'wrong','consumer_id':'peer','status':'accepted'}
        with self.db.read_view() as u: event=u.events.all()[0]
        with self.assertRaises(DomainError): await SocialConsumer('peer',wrong)(event)

    async def test_slow_consumer_times_out_and_does_not_block_other_consumer(self):
        self.apply('start_activity',{'name':'fact'})
        delivered=[]
        async def slow(event): await asyncio.Event().wait()
        async def fast(event): delivered.append(event.event_id); return event.event_id
        dispatcher=EventDispatcher(self.db,timeout_seconds=.01)
        dispatcher.subscribe('slow',slow); dispatcher.subscribe('fast',fast)
        await asyncio.wait_for(dispatcher.dispatch(),1)
        self.assertEqual(len(delivered),1)

    async def test_deadline_tick_advances_while_model_and_event_workers_wait(self):
        async def model(*args): await asyncio.Event().wait()
        runtime=DevelopmentRuntime(self.db,model,lock_path=Path(self.temp.name)/'lock').start()
        async def blocked(): await asyncio.Event().wait()
        runtime.process_triggers=blocked
        runtime.dispatcher.dispatch=blocked
        self.apply('start_sleep',{'duration_minutes':5},source='legacy_observed')
        worker=asyncio.create_task(runtime.run())
        try:
            await asyncio.sleep(.05)
            self.clock.advance(timedelta(minutes=6))
            await asyncio.sleep(1.05)
            self.assertEqual(self.engine.state().sleep_phase,'awake')
        finally: await runtime.close()
        self.assertTrue(worker.done()); self.assertFalse(runtime.background_tasks)
