"""Life Runtime assembled by the selected host adapter under one writer lease.

Integration Ready permits isolated and joint testing only; privileged external
identity and policy transports remain disabled until their contracts exist.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4
import asyncio

from ..coordinator.decisions import DecisionCoordinator, call_model
from ..domain.commands import Command
from ..domain.segment import SchedulerConfig
from ..planning.segments import SegmentService
from ..planning.availability import AvailabilityConfig
from ..domain.models import require
from ..events.dispatcher import EventDispatcher
from ..storage.safety import WriterLease
from .engine import LifeStateEngine
from .projections import LifeQueries
from .recovery import RecoveryService


@dataclass(frozen=True)
class LifeRuntimeConfig:
    planning_mode: str = 'autonomous'
    sleep_mode: str = 'autonomous'
    timezone: str = 'Asia/Shanghai'
    model_timeout_seconds: int = 90
    daily_budget: int = 20
    fallback_check_minutes: int = 45
    habits_enabled: bool = False
    cleanup_enabled: bool = False
    daily_planning_enabled: bool = False
    daily_planning_time: str = '08:00'
    initialize_body_state: bool = True
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    availability: AvailabilityConfig = field(default_factory=AvailabilityConfig)

    def __post_init__(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.timezone)
        require(type(self.initialize_body_state) is bool, 'invalid initialize body state flag')
        import re
        require(isinstance(self.daily_planning_time,str) and
                re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',self.daily_planning_time) is not None,
                'invalid daily planning time')
        require(self.planning_mode in ('legacy','autonomous'), 'invalid planning mode')
        require(self.sleep_mode in ('legacy_fixed','autonomous','disabled'), 'invalid sleep mode')
        require(self.sleep_mode!='autonomous' or self.planning_mode=='autonomous', 'autonomous sleep needs autonomous planning')
        require(type(self.fallback_check_minutes) is int and 30<=self.fallback_check_minutes<=60,'invalid fallback interval')
        require(type(self.model_timeout_seconds) is int and 1<=self.model_timeout_seconds<=300,'invalid model timeout')
        require(type(self.daily_budget) is int and 1<=self.daily_budget<=1000,'invalid decision budget')
        require(isinstance(self.scheduler, SchedulerConfig), 'invalid scheduler config')
        require(isinstance(self.availability, AvailabilityConfig), 'invalid availability config')
        require(not self.scheduler.enabled or self.planning_mode == 'autonomous', 'rolling segments need autonomous planning')



class DevelopmentRuntime:
    def __init__(self, database, model, *, config=None, lock_path):
        self.database,self.config=database,config or LifeRuntimeConfig()
        self.lease=WriterLease(lock_path)
        self.engine=LifeStateEngine(database, scheduler=self.config.scheduler)
        async def guarded_model(state,triggers,*,task_kind='present'):
            proposal=await call_model(model,state,triggers,task_kind)
            if task_kind == 'segment' and isinstance(proposal, dict) and 'kind' not in proposal:
                from ..domain.segment import validate_segment
                proposal = {'kind': 'advance_segment', 'payload': validate_segment(proposal)}
            if self.config.sleep_mode=='disabled':
                require(proposal.get('kind') not in ('start_sleep','extend_sleep'), 'sleep disabled')
                require(not (proposal.get('kind') == 'advance_segment' and proposal['payload']['event'] == '睡眠'), 'sleep disabled')
            return proposal
        self.coordinator=DecisionCoordinator(self.engine,guarded_model,daily_budget=self.decision_budget,
            timeout_seconds=self.config.model_timeout_seconds,
            budget_timezone=self.config.timezone)
        self.queries=LifeQueries(self.engine, availability=self.config.availability)
        self.recovery=RecoveryService(self.engine)
        self.recovery.rolling_segments = self.config.scheduler.enabled
        self.segments=SegmentService(self, self.config.scheduler)
        self.segment_due=False
        self.last_segment_check=None
        self.dispatcher=EventDispatcher(database)
        self.opened=False
        self.last_check=None
        self.stop_event=asyncio.Event()
        self.worker_task=None
        self.background_tasks=set()

    @property
    def decision_budget(self):
        return self.config.daily_budget

    def start(self):
        self.lease.acquire()
        try:
            self.engine.initialize()
            from .subjective import RULE_VERSION
            from ..domain.clock import to_epoch_ms
            with self.database.unit_of_work() as u:
                u.connection.execute('INSERT OR IGNORE INTO body_rule_cutovers VALUES(?,?)',
                    (RULE_VERSION,to_epoch_ms(self.engine.clock.now_utc())))
                u.commit()
            if self.config.planning_mode=='autonomous':
                with self.database.read_view() as u:
                    pending=u.connection.execute("SELECT count(*) FROM legacy_import_records WHERE review_reason IS NOT NULL").fetchone()[0]
                require(pending==0, 'migration review unresolved: autonomous staging blocked')
            from ..planning.conversation import expire
            expire(self, restart=True)
            self.coordinator.recover()
            self.recovery.startup()
            self.opened=True
        except BaseException:
            self.lease.release()
            raise
        return self

    async def request_replan(self, trigger_id):
        require(self.opened, 'runtime not started')
        require(self.config.planning_mode=='autonomous', 'legacy planner supplies proposals through bridge')
        return await self.coordinator.request(trigger_id)

    def propose_commitment(self, payload, *, key):
        state=self.engine.state()
        return self.engine.execute(Command(idempotency_key=key,kind='propose_commitment',payload=payload,expected_state_version=state.state_version))

    def tick(self):
        # Local deadlines remain synchronous. The asynchronous trigger worker
        # drains an optional rolling decision window without blocking recovery.
        require(self.opened, 'runtime not started')
        result=self.recovery.tick()
        mono=self.engine.clock.monotonic()
        if self.config.scheduler.enabled and (self.last_segment_check is None or
                mono-self.last_segment_check >= self.config.scheduler.decision_interval_minutes*60):
            self.last_segment_check=mono
            self.segment_due=True
            result['segment_due']=True
        if self.config.daily_planning_enabled and self.config.planning_mode=='autonomous':
            # One durable local-day trigger; coordinator owns budget and claims.
            now=self.engine.clock.now_utc()
            local=self.engine.clock.to_local(now,self.config.timezone)
            day=local.date().isoformat()
            key='daily-planning:'+day
            if local.strftime('%H:%M')>=self.config.daily_planning_time and getattr(self,'last_daily_trigger',None)!=key:
                with self.database.unit_of_work() as u:
                    u.connection.execute('INSERT OR IGNORE INTO decision_triggers VALUES(?,?,?,?,0)',
                        (key,'default','enqueue_replan',int(now.timestamp()*1000)))
                    u.commit()
                self.last_daily_trigger=key
        mono=self.engine.clock.monotonic()
        if self.last_check is None or mono-self.last_check>=self.config.fallback_check_minutes*60:
            self.last_check=mono
            result['fallback_checked']=True
            state=self.engine.state()
            now=self.engine.clock.now_utc()
            if now>state.last_advanced_at and now>=state.updated_at:
                self.engine.execute(Command(idempotency_key='advance:'+now.isoformat(),kind='advance',
                    expected_state_version=state.state_version,payload={'timezone':self.config.timezone,
                        'segment_energy_rates':self.config.scheduler.enabled}))
            if self.config.habits_enabled:
                with self.database.read_view() as u:
                    habits=u.habits.all()
                    has_evidence=any(a.ended_at is not None and a.role=='primary' for a in u.activities.all())
                last=max((h.last_reviewed_at for h in habits if h.last_reviewed_at),default=None)
                if has_evidence and (last is None or now-last>=timedelta(days=7)):
                    self.engine.execute(Command(idempotency_key='habits:'+now.isoformat(),kind='review_habits',
                        expected_state_version=self.engine.state().state_version,payload={'timezone':self.config.timezone}))
        return result

    async def process_triggers(self):
        """Drain durable triggers and at most one due rolling window."""
        require(self.opened, 'runtime not started')
        if self.config.planning_mode!='autonomous': return {'status':'not_eligible'}
        if self.engine.state().sleep_phase=='sleeping' and not self.config.scheduler.enabled: return {'status':'not_eligible'}
        from ..planning.conversation import expire
        expire(self)
        with self.database.read_view() as u:
            ids=[row[0] for row in u.connection.execute('SELECT trigger_id FROM decision_triggers WHERE consumed=0 ORDER BY created_at_ms,trigger_id LIMIT 100')]
        if not ids:
            if self.segment_due:
                self.segment_due=False
                return await self.segments.tick()
            return {'status':'no_triggers'}
        # Reference planning never consumes activity triggers. One RPC path per
        # turn; all actual activity triggers are coalesced into a rolling decision.
        planning=[key for key in ids if key.startswith(('daily-planning:','manual-planning:'))]
        if planning:
            result=await self.coordinator.request('batch:'+','.join(planning))
            if result['status']=='budget_exhausted':
                with self.database.unit_of_work() as u:
                    u.connection.executemany('UPDATE decision_triggers SET consumed=1 WHERE trigger_id=?',[(key,) for key in planning])
                    u.commit()
                self.coordinator.pending.clear()
            return result
        if self.engine.state().active_sleep_session_id:
            return {'status':'sleeping'}
        self.segment_due=False
        result=await self.coordinator.request('batch:'+','.join(ids))
        if self.config.scheduler.enabled and result['status'] in ('budget_exhausted','invalid','uncertain','rejected'):
            # Budget deferral must not become a once-per-second fallback loop.
            with self.database.unit_of_work() as u:
                u.connection.executemany('UPDATE decision_triggers SET consumed=1 WHERE trigger_id=?',[(key,) for key in ids])
                u.commit()
            self.coordinator.pending.clear()
            return self.segments._continue(result['status'])
        # Coordinator durably claims the batch before RPC. Post-await claiming
        # would replay a cancelled/unknown model request after restart.
        return result

    async def run(self):
        """Local deadline loop, independent of the 45-minute estimate check.

        The embedding Runner explicitly starts this coroutine. No SDK or
        network is used except injected model/consumer ports.
        """
        require(self.opened and self.worker_task is None,'runtime loop already running/not started')
        self.worker_task=asyncio.current_task()
        async def background(operation):
            while not self.stop_event.is_set():
                await operation()
                try: await asyncio.wait_for(self.stop_event.wait(),1)
                except TimeoutError: pass
        try:
            while not self.stop_event.is_set():
                if not self.background_tasks:
                    self.background_tasks={asyncio.create_task(background(self.process_triggers)),
                                           asyncio.create_task(background(self.dispatcher.dispatch))}
                for task in self.background_tasks:
                    if task.done():
                        await task  # Surface worker failures to the host readiness gate.
                        raise RuntimeError('life background worker stopped')
                self.tick()
                try: await asyncio.wait_for(self.stop_event.wait(),1)
                except TimeoutError: pass
        finally:
            for task in self.background_tasks: task.cancel()
            await asyncio.gather(*self.background_tasks,return_exceptions=True)
            self.background_tasks.clear()
            self.worker_task=None

    def switch_modes(self, config):
        state=self.engine.state()
        self.engine.execute(Command(idempotency_key=uuid4().hex,kind='configure',payload={},source='authorized',expected_state_version=state.state_version))
        self.config=config
        self.queries.availability=config.availability
        self.coordinator.daily_budget=self.decision_budget
        self.coordinator.budget_timezone=config.timezone
        self.engine.scheduler=config.scheduler
        self.segments.config=config.scheduler
        self.recovery.rolling_segments=config.scheduler.enabled
        self.last_segment_check=None
        self.segment_due=False
        self.coordinator.timeout=config.model_timeout_seconds

    async def close(self):
        try:
            self.stop_event.set()
            worker=self.worker_task
            if worker is not None and worker is not asyncio.current_task():
                worker.cancel()
                await asyncio.gather(worker,return_exceptions=True)
            await self.coordinator.close()
        finally:
            self.lease.release()
            self.opened=False
