"""One in-flight model call per character; persisted basis and budget audit."""
import asyncio
import json
import inspect
from datetime import timedelta
from uuid import uuid4

from ..domain.clock import to_epoch_ms, local_day_bounds
from ..domain.commands import Command
from ..domain.models import DomainError, json_text, require
from ..life_state.engine import digest
from ..planning.hybrid import decision_task_kind, validate_task_candidate, validate_plan_quality


async def call_model(model, state, triggers, task_kind):
    # Older internal injected ports take two arguments. Inspect before calling:
    # a TypeError from inside an RPC must never cause an accidental extra call.
    parameters=inspect.signature(model).parameters.values()
    supports_task=any(p.name=='task_kind' or p.kind==inspect.Parameter.VAR_KEYWORD for p in parameters)
    if supports_task: return await model(state,triggers,task_kind=task_kind)
    return await model(state,triggers)


class DecisionCoordinator:
    def __init__(self, engine, model, *, daily_budget=24, cooldown_seconds=60, timeout_seconds=30, budget_timezone=None):
        require(type(daily_budget) is int and 1 <= daily_budget <= 1000, 'invalid model budget')
        require(0 <= cooldown_seconds <= 3600 and 0 < timeout_seconds <= 300, 'invalid decision timing')
        self.engine, self.model = engine, model  # model is an injected internal callable
        self.daily_budget, self.cooldown, self.timeout = daily_budget,cooldown_seconds,timeout_seconds
        self.tasks, self.pending, self.last_monotonic = {},{},{}
        self.closed = False
        self.budget_timezone = budget_timezone

    def _budget_start(self, now):
        if self.budget_timezone:
            local = self.engine.clock.to_local(now, self.budget_timezone)
            start, _ = local_day_bounds(local.date(), self.budget_timezone)
            return to_epoch_ms(start)
        return to_epoch_ms(now-timedelta(days=1))

    def recover(self):
        # Call only after exclusive writer ownership is acquired. Unknown RPC
        # outcomes count toward budget and are never retried automatically.
        with self.engine.database.unit_of_work() as u:
            u.connection.execute("UPDATE decision_runs SET status='abandoned',diagnostic='process_restart_unknown_outcome' WHERE status='running'")
            # Compatibility with earlier runtimes which acknowledged a durable
            # trigger only AFTER awaiting the model. Any persisted attempt has
            # already spent its opportunity, including uncertain/cancelled ones.
            for row in u.connection.execute('SELECT character_id,trigger_ids_json FROM decision_runs'):
                for trigger in json.loads(row[1]):
                    if isinstance(trigger,str) and trigger.startswith('batch:'):
                        u.connection.executemany('UPDATE decision_triggers SET consumed=1 WHERE trigger_id=? AND character_id=?',
                                                 [(key,row[0]) for key in trigger[6:].split(',') if key])
            u.commit()

    async def request(self, trigger_id, *, character_id='default'):
        require(not self.closed, 'coordinator is closed')
        self.pending.setdefault(character_id,set()).add(trigger_id)
        task = self.tasks.get(character_id)
        if task is None or task.done():
            task = asyncio.create_task(self._decide(character_id))
            self.tasks[character_id] = task
        return await asyncio.shield(task)

    async def _decide(self, char):
        await asyncio.sleep(0)  # merge simultaneous event bursts
        state = self.engine.state(char)
        now = self.engine.clock.now_utc()
        mono = self.engine.clock.monotonic()
        if char in self.last_monotonic and mono - self.last_monotonic[char] < self.cooldown:
            return {'status':'cooldown'}
        require(now >= state.updated_at, 'clock rollback blocks new decisions')
        decision_id = uuid4().hex
        with self.engine.database.unit_of_work() as u:
            recent = list(u.connection.execute('SELECT started_at_ms FROM decision_runs WHERE character_id=? AND started_at_ms>=?',
                (char,self._budget_start(now))))
            if len(recent) >= self.daily_budget: return {'status':'budget_exhausted'}
            if recent and to_epoch_ms(now)-max(r[0] for r in recent) < self.cooldown*1000: return {'status':'cooldown'}
            triggers = sorted(self.pending.pop(char,set()))
            durable=set()
            explicit=[]
            for trigger in triggers:
                if not trigger.startswith('batch:'):
                    explicit.append(trigger)
                    continue
                for key in trigger[6:].split(','):
                    row=u.connection.execute('SELECT consumed FROM decision_triggers WHERE trigger_id=? AND character_id=?',(key,char)).fetchone()
                    require(row is not None,'unknown durable decision trigger')
                    if not row[0]: durable.add(key)
            triggers=sorted(explicit+(['batch:'+','.join(sorted(durable))] if durable else []))
            if not triggers: return {'status':'no_triggers'}
            u.connection.execute('INSERT INTO decision_runs (decision_id,character_id,basis_state_version,basis_plan_version,basis_config_revision,started_at_ms,trigger_ids_json,status) VALUES (?,?,?,?,?,?,?,?)',
                (decision_id,char,state.state_version,state.plan_version,state.config_revision,to_epoch_ms(now),json_text(triggers),'running'))
            # Claim exactly the persisted batch in the SAME transaction as its
            # attempt record, before crossing the uncertain external RPC edge.
            u.connection.executemany('UPDATE decision_triggers SET consumed=1 WHERE trigger_id=? AND character_id=?',
                                     [(key,char) for key in sorted(durable)])
            u.commit()
        self.last_monotonic[char] = mono
        task_kind=decision_task_kind(triggers)
        for attempt in range(2):
            if attempt:
                current=self.engine.state(char)
                if self._basis(current)!=self._basis(state):
                    return {'status':'stale','decision_id':decision_id}
                now=self.engine.clock.now_utc()
                with self.engine.database.unit_of_work() as u:
                    used=u.connection.execute('SELECT count(*) FROM decision_runs WHERE character_id=? AND started_at_ms>=?',
                        (char,self._budget_start(now))).fetchone()[0]
                    if used>=self.daily_budget:
                        u.connection.execute("UPDATE decision_runs SET diagnostic='degraded:invalid_retry_budget_exhausted' WHERE decision_id=?",(decision_id,))
                        u.commit()
                        return {'status':'invalid','decision_id':decision_id,'degraded':True}
                    decision_id=uuid4().hex
                    u.connection.execute('INSERT INTO decision_runs (decision_id,character_id,basis_state_version,basis_plan_version,basis_config_revision,started_at_ms,trigger_ids_json,status) VALUES (?,?,?,?,?,?,?,?)',
                        (decision_id,char,state.state_version,state.plan_version,state.config_revision,to_epoch_ms(now),json_text(triggers),'running'))
                    u.commit()
                self.last_monotonic[char]=self.engine.clock.monotonic()
            try:
                # Give the model port time to record its own timeout before cancellation.
                proposal=await asyncio.wait_for(call_model(self.model,state,tuple(triggers),task_kind),self.timeout+10)
                proposal=validate_task_candidate(proposal,task_kind)
            except asyncio.CancelledError:
                self._finish(decision_id,'uncertain','degraded:cancelled_unknown_outcome')
                raise
            except (TimeoutError,ConnectionError,OSError):
                self._finish(decision_id,'uncertain','degraded:rpc_outcome_unknown_no_retry')
                return {'status':'uncertain','decision_id':decision_id,'degraded':True}
            except Exception as exc:
                self._finish(decision_id,'invalid',('degraded:' if attempt else '')+type(exc).__name__)
                if not attempt: continue
                return {'status':'invalid','decision_id':decision_id,'degraded':True}
            current=self.engine.state(char)
            if self._basis(current)!=self._basis(state):
                self._finish(decision_id,'stale','basis_changed_while_awaiting_model')
                return {'status':'stale','decision_id':decision_id}
            diagnostic=validate_plan_quality(proposal) if task_kind=='planning' else None
            self._finish(decision_id,'validated',diagnostic,proposal)
            try:
                result=self.engine.execute(Command(idempotency_key='decision:'+decision_id,kind=proposal['kind'],payload=proposal['payload'],
                    character_id=char,source='model',decision_id=decision_id,expected_state_version=state.state_version,
                    expected_plan_version=state.plan_version,expected_config_revision=state.config_revision,
                    expires_at=now+timedelta(minutes=5)))
            except DomainError as exc:
                stale='stale' in str(exc)
                self._finish(decision_id,'stale' if stale else 'invalid',
                    ('degraded:' if attempt and not stale else '')+type(exc).__name__)
                if not stale and not attempt: continue
                return {'status':'rejected','decision_id':decision_id,'degraded':not stale}
            quality={'degraded':True} if diagnostic and diagnostic.startswith('degraded:') else {}
            return {'status':'applied','decision_id':decision_id,**result,**quality}

    @staticmethod
    def _basis(state):
        return state.state_version,state.plan_version,state.config_revision

    def _finish(self, decision_id, status, diagnostic, proposal=None):
        with self.engine.database.unit_of_work() as u:
            u.connection.execute('UPDATE decision_runs SET status=?,finished_at_ms=?,candidate_json=?,candidate_hash=?,diagnostic=? WHERE decision_id=?',
                (status,to_epoch_ms(self.engine.clock.now_utc()),json_text(proposal) if proposal else None,
                 digest(proposal) if proposal else None,diagnostic,decision_id))
            u.commit()

    async def close(self):
        self.closed = True
        tasks=[task for task in self.tasks.values() if not task.done()]
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.pending.clear()
