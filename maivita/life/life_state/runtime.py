"""Life Runtime assembled by the selected host adapter under one writer lease.

Integration Ready permits isolated and joint testing only; privileged external
identity and policy transports remain disabled until their contracts exist.
"""
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4
import asyncio

from ..coordinator.decisions import DecisionCoordinator
from ..domain.commands import Command
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
    daily_budget: int = 24
    fallback_check_minutes: int = 45
    habits_enabled: bool = False
    cleanup_enabled: bool = False

    def __post_init__(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.timezone)
        require(self.planning_mode in ('legacy','autonomous'), 'invalid planning mode')
        require(self.sleep_mode in ('legacy_fixed','autonomous','disabled'), 'invalid sleep mode')
        require(self.sleep_mode!='autonomous' or self.planning_mode=='autonomous', 'autonomous sleep needs autonomous planning')
        require(type(self.fallback_check_minutes) is int and 30<=self.fallback_check_minutes<=60,'invalid fallback interval')
        require(type(self.daily_budget) is int and 1<=self.daily_budget<=1000,'invalid decision budget')

    @classmethod
    def from_r0(cls, config, *, autonomous=False):
        require(not autonomous or not config.compatibility.legacy_empty_admin_all,
            'resolve legacy empty-admin semantics before autonomous mode')
        return cls(planning_mode='autonomous' if autonomous else 'legacy',
            sleep_mode='disabled' if config.sleep.mode=='disabled' else 'autonomous' if autonomous else 'legacy_fixed',
            timezone=config.character.timezone)


class DevelopmentRuntime:
    def __init__(self, database, model, *, config=None, lock_path):
        self.database,self.config=database,config or LifeRuntimeConfig()
        self.lease=WriterLease(lock_path)
        self.engine=LifeStateEngine(database)
        async def guarded_model(state,triggers):
            proposal=await model(state,triggers)
            if self.config.sleep_mode=='disabled':
                require(proposal.get('kind') not in ('start_sleep','extend_sleep'), 'sleep disabled')
            return proposal
        self.coordinator=DecisionCoordinator(self.engine,guarded_model,daily_budget=self.config.daily_budget)
        self.queries=LifeQueries(self.engine)
        self.recovery=RecoveryService(self.engine)
        self.dispatcher=EventDispatcher(database)
        self.opened=False
        self.last_check=None
        self.stop_event=asyncio.Event()
        self.worker_task=None

    def start(self):
        self.lease.acquire()
        try:
            self.engine.initialize()
            if self.config.planning_mode=='autonomous':
                with self.database.read_view() as u:
                    pending=u.connection.execute("SELECT count(*) FROM legacy_import_records WHERE review_reason IS NOT NULL").fetchone()[0]
                require(pending==0, 'migration review unresolved: autonomous staging blocked')
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
        # No LLM polling: the fallback reports a check result; model decisions
        # require an explicit significant trigger through request_replan.
        require(self.opened, 'runtime not started')
        result=self.recovery.tick()
        mono=self.engine.clock.monotonic()
        if self.last_check is None or mono-self.last_check>=self.config.fallback_check_minutes*60:
            self.last_check=mono
            result['fallback_checked']=True
            state=self.engine.state()
            now=self.engine.clock.now_utc()
            if now>state.last_advanced_at and now>=state.updated_at:
                self.engine.execute(Command(idempotency_key='advance:'+now.isoformat(),kind='advance',
                    expected_state_version=state.state_version,payload={'timezone':self.config.timezone}))
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
        """An isolated Runner can call this after tick; no polling LLM calls."""
        require(self.opened, 'runtime not started')
        if self.config.planning_mode!='autonomous' or self.engine.state().sleep_phase=='sleeping': return {'status':'not_eligible'}
        with self.database.read_view() as u:
            ids=[row[0] for row in u.connection.execute('SELECT trigger_id FROM decision_triggers WHERE consumed=0 ORDER BY created_at_ms,trigger_id LIMIT 100')]
        if not ids: return {'status':'no_triggers'}
        # Coalesce all durable triggers into one model call. Coordinator still
        # enforces daily budget/cooldown; pending rows survive deferral.
        result=await self.coordinator.request('batch:'+','.join(ids))
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
        try:
            while not self.stop_event.is_set():
                self.tick()
                await self.process_triggers()
                await self.dispatcher.dispatch()
                try: await asyncio.wait_for(self.stop_event.wait(),1)
                except TimeoutError: pass
        finally: self.worker_task=None

    def switch_modes(self, config):
        state=self.engine.state()
        self.engine.execute(Command(idempotency_key=uuid4().hex,kind='configure',payload={},source='authorized',expected_state_version=state.state_version))
        self.config=config
        self.coordinator.daily_budget=config.daily_budget

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
