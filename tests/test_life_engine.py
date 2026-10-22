from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from r1_support import AT,clock
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.domain.models import Commitment,DomainError
from maibot_autonomous_planning_plugin.maivita.life.life_state.engine import LifeStateEngine
from maibot_autonomous_planning_plugin.maivita.life.life_state.projections import LifeQueries,QueryScope
from maibot_autonomous_planning_plugin.maivita.life.life_state.recovery import RecoveryService
from maibot_autonomous_planning_plugin.maivita.compatibility.life_bridge import LegacyLifeBridge
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase


class EngineFixture:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.clock=clock()
        self.db=LifeDatabase.create(Path(self.temp.name).resolve()/'maivita.db',clock=self.clock)
        self.engine=LifeStateEngine(self.db)
        self.engine.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def command(self,kind,payload=None,**kwargs):
        return Command(kind=kind,payload=payload or {},idempotency_key=kwargs.pop('key',kind+str(self.engine.state().state_version)),
            expected_state_version=self.engine.state().state_version,**kwargs)

    def apply(self,kind,payload=None,**kwargs):
        return self.engine.execute(self.command(kind,payload,**kwargs))


class EngineTests(EngineFixture,unittest.TestCase):
    def test_T07_real_process_exit_around_command_commit_keeps_receipt_and_outbox_atomic(self):
        from r1_support import ROOT
        script="""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from r1_support import clock
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase
from maibot_autonomous_planning_plugin.maivita.life.life_state.engine import LifeStateEngine
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
def fault(point):
    if point==sys.argv[3]: os._exit(71)
db=LifeDatabase(Path(sys.argv[2]),clock=clock())
LifeStateEngine(db,fault=fault).execute(Command(idempotency_key='crash',kind='start_activity',payload={'name':'fact'},expected_state_version=0))
"""
        for point,expected in [('before_commit',0),('after_commit',1)]:
            with self.subTest(point=point):
                db=LifeDatabase.create(Path(self.temp.name).resolve()/(point+'.db'),clock=self.clock)
                engine=LifeStateEngine(db); engine.initialize()
                child=subprocess.run([sys.executable,'-c',script,str(ROOT),str(db.path),point],capture_output=True,timeout=15)
                self.assertEqual(child.returncode,71,child.stderr.decode(errors='replace'))
                with db.read_view() as u:
                    self.assertEqual(u.states.get('default').state_version,expected)
                    self.assertEqual(len(u.events.all()),expected)
                    self.assertEqual(u.connection.execute('SELECT count(*) FROM command_receipts').fetchone()[0],expected)
                if expected:
                    command=Command(idempotency_key='crash',kind='start_activity',payload={'name':'fact'},expected_state_version=0)
                    self.assertEqual(engine.execute(command),engine.execute(command))

    def test_T07_T08_receipt_outbox_atomic_before_and_after_crash(self):
        cmd=self.command('start_activity',{'name':'Study'})
        for point in ('after_state','before_commit'):
            def fail(actual):
                if actual==point: raise OSError('injected write failure')
            self.engine.fault=fail
            with self.assertRaises(OSError): self.engine.execute(cmd)
            with self.db.unit_of_work() as u:
                self.assertEqual(u.states.get('default').state_version,0)
                self.assertEqual(u.activities.all(),[])
                self.assertEqual(u.events.all(),[])
                self.assertEqual(u.connection.execute('SELECT count(*) FROM command_receipts').fetchone()[0],0)
        self.engine.fault=lambda point: (_ for _ in ()).throw(OSError('postcommit crash')) if point=='after_commit' else None
        with self.assertRaises(OSError): self.engine.execute(cmd)
        self.engine=LifeStateEngine(self.db)
        result=self.engine.execute(cmd)
        self.assertEqual(result,self.engine.execute(cmd))
        with self.db.unit_of_work() as u:
            self.assertEqual(len(u.activities.all()),1); self.assertEqual(len(u.events.all()),1)
        with self.assertRaisesRegex(DomainError,'different payload'):
            self.engine.execute(replace(cmd,payload={'name':'Different'}))

    def test_T08_two_commands_same_basis_only_one_wins(self):
        commands=[self.command('start_activity',{'name':name},key=name) for name in ('a','b')]
        def run(command):
            try: return self.engine.execute(command)
            except DomainError: return None
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(run,commands))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_T14_planned_sleep_is_not_actual_and_free_time_valid(self):
        self.apply('apply_plan_patch',{'local_date':'2026-10-21','reason':'free time','upserts':[
            {'item_id':'sleep-plan','name':'Maybe rest','time_mode':'unscheduled','kind':'sleep'}]})
        self.assertIsNone(self.engine.state().active_sleep_session_id)
        with self.db.unit_of_work() as u: self.assertEqual(u.activities.all(),[])
        self.apply('apply_plan_patch',{'reason':'empty plan','remove_ids':['sleep-plan']})
        self.assertEqual(self.engine.state().plan_version,2)

    def test_T19_commitment_conflict_preserves_snapshot_and_revision(self):
        with self.db.unit_of_work() as u:
            u.commitments.add(Commitment(commitment_id='c',character_id='default',title='private promise',
                source_stream_id='private',provenance='fixture',start_at=AT+timedelta(hours=1),end_at=AT+timedelta(hours=2),
                status='confirmed',confirmation_basis='explicit',visibility='source_stream'))
            u.commit()
        with self.assertRaisesRegex(DomainError,'conflicts'):
            self.apply('apply_plan_patch',{'local_date':'2026-10-21','reason':'bad','upserts':[
                {'item_id':'i','name':'conflict','time_mode':'fixed','start_at':(AT+timedelta(hours=1)).isoformat(),'end_at':(AT+timedelta(hours=3)).isoformat()}]})
        self.assertEqual(self.engine.state().state_version,0)
        with self.db.unit_of_work() as u: self.assertEqual(u.revisions.all(),[])
        queries=LifeQueries(self.engine)
        self.assertEqual(queries.get_plan(QueryScope('group'))['commitments'],[])
        self.assertEqual(queries.get_plan(QueryScope('private'))['commitments'][0]['title'],'private promise')

    def test_T10_fixed_bridge_records_only_observed_interval_and_recovers_once(self):
        bridge=LegacyLifeBridge(self.engine)
        bridge.observe_fixed_sleep(True,window_end=AT+timedelta(hours=1))
        self.assertEqual(bridge.get_current_activity()['activity_state'],'sleeping')
        self.clock.advance(timedelta(hours=2))
        recovery=RecoveryService(LifeStateEngine(self.db))
        self.assertEqual(recovery.tick()['status'],'wake')
        self.assertFalse(recovery.tick()['write'])
        with self.db.unit_of_work() as u:
            session=u.sessions.all()[0]
            self.assertEqual(session.started_at,AT)
            self.assertEqual(session.actual_sleep_minutes,60)
            self.assertEqual(len(u.activities.all()),1)
        self.assertEqual(self.engine.state().last_wake_at,AT+timedelta(hours=1))

    def test_T11_shared_fact_private_activity_redaction(self):
        self.apply('start_activity',{'name':'private activity'})
        queries=LifeQueries(self.engine)
        views=[queries.get_life_state(QueryScope(s)) for s in ('g1','g2','private')]
        self.assertTrue(all(v==views[0] for v in views)); self.assertIsNone(views[0]['activity'])
        self.assertEqual(queries.get_life_state(QueryScope(private_details=True))['activity']['name'],'private activity')
        with self.db.unit_of_work() as u: self.assertNotIn('private activity',json.dumps(dict(u.events.all()[0].payload)))

    def test_T09_clock_rollback_never_rewrites_history(self):
        self.apply('start_activity',{'name':'known'})
        self.clock.set_utc(AT-timedelta(hours=1))
        self.assertEqual(RecoveryService(self.engine).tick()['status'],'clock_rollback')
        with self.assertRaisesRegex(DomainError,'rollback'): self.apply('advance')
        self.assertEqual(self.engine.state().state_version,1)

    def test_autonomous_sleep_cannot_be_forged_by_internal_or_decision_id(self):
        with self.assertRaises(DomainError): self.apply('start_sleep',{'duration_minutes':480})
        with self.assertRaisesRegex(DomainError,'unverified'): self.apply('start_sleep',{'duration_minutes':480},source='model',decision_id='fake')

    def test_estimates_unknown_then_explicit_low_confidence_bounded_and_independent(self):
        self.apply('advance'); self.assertIsNone(self.engine.state().energy)
        self.apply('advance',{'initialize_neutral':True})
        self.apply('start_activity',{'name':'quiet','category':'entertainment'})
        self.clock.advance(timedelta(hours=24))
        self.apply('advance')
        state=self.engine.state()
        self.assertEqual((state.energy,state.fatigue,state.stress),(49,51,49.5))
        self.assertEqual(state.confidence,'low')
