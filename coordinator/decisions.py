"""One in-flight model call per character; persisted basis and budget audit."""
import asyncio
from datetime import timedelta
from uuid import uuid4

from ..domain.clock import to_epoch_ms
from ..domain.commands import Command
from ..domain.models import DomainError, json_text, require
from ..life_state.engine import digest
from ..planning.hybrid import candidate


class DecisionCoordinator:
    def __init__(self, engine, model, *, daily_budget=24, cooldown_seconds=60, timeout_seconds=30):
        require(type(daily_budget) is int and 1 <= daily_budget <= 1000, 'invalid model budget')
        require(0 <= cooldown_seconds <= 3600 and 0 < timeout_seconds <= 300, 'invalid decision timing')
        self.engine, self.model = engine, model  # model is an injected internal callable
        self.daily_budget, self.cooldown, self.timeout = daily_budget,cooldown_seconds,timeout_seconds
        self.tasks, self.pending, self.last_monotonic = {},{},{}
        self.closed = False

    def recover(self):
        # Call only after exclusive writer ownership is acquired. Unknown RPC
        # outcomes count toward budget and are never retried automatically.
        with self.engine.database.unit_of_work() as u:
            u.connection.execute("UPDATE decision_runs SET status='abandoned',diagnostic='process_restart_unknown_outcome' WHERE status='running'")
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
            recent = list(u.connection.execute('SELECT started_at_ms FROM decision_runs WHERE character_id=? AND started_at_ms>?',
                (char,to_epoch_ms(now-timedelta(days=1)))))
            if len(recent) >= self.daily_budget: return {'status':'budget_exhausted'}
            if recent and to_epoch_ms(now)-max(r[0] for r in recent) < self.cooldown*1000: return {'status':'cooldown'}
            triggers = sorted(self.pending.pop(char,set()))
            u.connection.execute('INSERT INTO decision_runs (decision_id,character_id,basis_state_version,basis_plan_version,basis_config_revision,started_at_ms,trigger_ids_json,status) VALUES (?,?,?,?,?,?,?,?)',
                (decision_id,char,state.state_version,state.plan_version,state.config_revision,to_epoch_ms(now),json_text(triggers),'running'))
            u.commit()
        self.last_monotonic[char] = mono
        try:
            proposal = await asyncio.wait_for(self.model(state,tuple(triggers)),self.timeout)
            proposal = candidate(proposal)
        except asyncio.CancelledError:
            self._finish(decision_id,'uncertain','cancelled_unknown_outcome')
            raise
        except (TimeoutError,ConnectionError):
            self._finish(decision_id,'uncertain','rpc_outcome_unknown_no_retry')
            return {'status':'uncertain','decision_id':decision_id}
        except Exception as exc:
            self._finish(decision_id,'invalid',type(exc).__name__)
            return {'status':'invalid','decision_id':decision_id}
        current = self.engine.state(char)
        if (current.state_version,current.plan_version,current.config_revision) != (state.state_version,state.plan_version,state.config_revision):
            self._finish(decision_id,'stale','basis_changed_while_awaiting_model')
            return {'status':'stale','decision_id':decision_id}
        self._finish(decision_id,'validated',None,proposal)
        try:
            result = self.engine.execute(Command(idempotency_key='decision:'+decision_id,kind=proposal['kind'],payload=proposal['payload'],
                character_id=char,source='model',decision_id=decision_id,expected_state_version=state.state_version,
                expected_plan_version=state.plan_version,expected_config_revision=state.config_revision,
                expires_at=now+timedelta(minutes=5)))
        except DomainError as exc:
            self._finish(decision_id,'stale' if 'stale' in str(exc) else 'invalid',type(exc).__name__)
            return {'status':'rejected','decision_id':decision_id}
        return {'status':'applied','decision_id':decision_id,**result}

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
