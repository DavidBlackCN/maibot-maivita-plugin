"""Phase 1 product invariants against domain code and temporary SQLite."""
import unittest
from datetime import timedelta
from dataclasses import replace
from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.segment import SchedulerConfig
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime,LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.life.life_state.projections import LifeQueries,QueryScope
from maibot_autonomous_planning_plugin.maivita.integrations.social import public_events


class PhaseOneLife(EngineFixture,unittest.IsolatedAsyncioTestCase):
    def segment(self,**changes):
        self.engine.scheduler=SchedulerConfig(enabled=True)
        return self.apply('advance_segment',{'event':'阅读','category':'rest','location':'家','emotion':'平静',
            'minutes':45,'continue':False,**changes})

    def test_public_context_atomic_private_and_gaps(self):
        self.apply('start_activity',{'name':'private secret','category':'work','visibility':'restricted'})
        data=LifeQueries(self.engine).get_public_life_context(QueryScope(private_details=True))
        self.assertIsNone(data['activity']); self.assertEqual(data['history'],[])
        self.clock.advance(timedelta(minutes=1))
        self.segment()
        self.clock.advance(timedelta(minutes=20))
        data=LifeQueries(self.engine).get_public_life_context()
        self.assertEqual(data['activity']['location'],'家')
        self.assertEqual(data['activity']['duration_minutes'],20)
        self.assertNotIn('private secret',str(data))
        self.assertNotIn('plan',data)

    def test_start_event_once_and_switch_is_not_completion(self):
        self.segment(); first=self.engine.state().primary_activity_id
        self.clock.advance(timedelta(minutes=2)); self.segment(**{'continue':True})
        events=public_events(self.db)['events']
        self.assertEqual(sum(e['event_type']=='life.activity.started' for e in events),1)
        self.clock.advance(timedelta(minutes=2)); self.segment(event='散步',category='travel')
        with self.db.read_view() as u: self.assertEqual(u.activities.get(first).status,'interrupted')

    async def test_single_path_budget_failure_and_bounded_check(self):
        calls=[]
        async def model(state,triggers,*,task_kind):
            calls.append(task_kind)
            raise ConnectionError('isolated failure')
        runtime=DevelopmentRuntime(self.db,model,config=LifeRuntimeConfig(daily_budget=1,
            scheduler=SchedulerConfig(enabled=True)),lock_path=self.db.path.with_suffix('.lock')).start()
        try:
            runtime.tick(); await runtime.process_triggers()
            self.assertEqual(calls,['segment'])
            self.assertIsNotNone(self.engine.state().primary_activity_id)
            self.clock.advance(timedelta(minutes=15));runtime.tick();await runtime.process_triggers()
            self.assertEqual(len(calls),1)
            data=runtime.queries.get_life_state()
            self.assertIsNotNone(data['next_deadline_at'])
        finally: await runtime.close()

    async def test_sleep_cannot_be_replaced_by_threshold_or_periodic_check(self):
        calls=[]
        async def model(*args,**kwargs): calls.append(1);raise AssertionError('sleep must not call model')
        runtime=DevelopmentRuntime(self.db,model,config=LifeRuntimeConfig(
            scheduler=SchedulerConfig(enabled=True)),lock_path=self.db.path.with_suffix('.lock')).start()
        try:
            self.apply('start_sleep',{'duration_minutes':60},source='authorized')
            self.clock.advance(timedelta(minutes=15));runtime.tick();await runtime.process_triggers()
            self.assertEqual(calls,[]);self.assertEqual(self.engine.state().sleep_phase,'sleeping')
            self.clock.advance(timedelta(minutes=46));runtime.tick()
            self.assertNotEqual(self.engine.state().sleep_phase,'sleeping')
        finally: await runtime.close()
