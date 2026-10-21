"""Deterministic persisted-deadline recovery. Never invokes an LLM."""
from ..domain.commands import Command
from ..domain.clock import to_epoch_ms


class RecoveryService:
    def __init__(self, engine):
        self.engine = engine

    def startup(self, character_id='default'):
        state=self.engine.state(character_id)
        now=self.engine.clock.now_utc()
        if state.active_sleep_session_id is None and state.primary_activity_id is not None and (now-state.updated_at).total_seconds()>60:
            self.engine.execute(Command(idempotency_key='recovery-gap:'+state.updated_at.isoformat(),kind='recover_observation_gap',
                character_id=character_id,expected_state_version=state.state_version,payload={'start_at':state.updated_at.isoformat()}))
        return self.tick(character_id)

    def tick(self, character_id='default'):
        state = self.engine.state(character_id)
        now = self.engine.clock.now_utc()
        if now < state.updated_at:
            return {'status':'clock_rollback', 'write':False}
        if not state.active_sleep_session_id:
            with self.engine.database.read_view() as u:
                planned=u.connection.execute("""SELECT p.item_id,p.plan_version FROM plan_item_checks p JOIN schedule_items i ON i.item_id=p.item_id
                    WHERE i.character_id=? AND i.plan_id=? AND i.status='planned' AND p.processed=0 AND p.deadline_at_ms<=?
                    ORDER BY p.deadline_at_ms,p.item_id LIMIT 1""",(character_id,state.active_plan_id,to_epoch_ms(now))).fetchone()
            if planned:
                result=self.engine.execute(Command(idempotency_key=f'plan-check:{planned[0]}:{planned[1]}',kind='check_plan_item',
                    expected_state_version=state.state_version,character_id=character_id,payload={'item_id':planned[0]}))
                return {'status':'plan_check_due','write':True,**result}
            if state.primary_activity_id:
                with self.engine.database.read_view() as u:
                    row=u.connection.execute('SELECT * FROM activity_checkpoints WHERE activity_id=?',(state.primary_activity_id,)).fetchone()
                if row and not row['processed'] and to_epoch_ms(now)>=row['deadline_at_ms']:
                    result=self.engine.execute(Command(idempotency_key='checkpoint:'+state.primary_activity_id,kind='check_activity',
                        character_id=character_id,expected_state_version=state.state_version,payload={'activity_id':state.primary_activity_id}))
                    return {'status':'activity_check_due','write':True,**result}
            return {'status':'no_deadline', 'write':False}
        with self.engine.database.unit_of_work() as u:
            session = u.sessions.get(state.active_sleep_session_id)
        if now >= min(session.expected_wake_at, session.hard_deadline_at):
            kind, deadline = 'wake', min(session.expected_wake_at, session.hard_deadline_at)
        elif session.status == 'interrupted' and now >= session.interrupt_until:
            kind, deadline = 'resume_sleep', session.interrupt_until
        else:
            return {'status':'waiting', 'next_deadline':session.interrupt_until or session.expected_wake_at, 'write':False}
        result = self.engine.execute(Command(idempotency_key=f'deadline:{session.session_id}:{session.revision}:{kind}',
            kind=kind, character_id=character_id, expected_state_version=state.state_version, payload={'reason':'deadline'}))
        return {'status':kind, 'write':True, **result}
