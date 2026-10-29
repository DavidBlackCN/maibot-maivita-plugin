"""Rolling decisions reuse activities, checkpoints and the shared RPC budget.

ended_at is always an observed end. A checkpoint is the expected end, and
continuing changes only that expectation, never previously observed context.
"""
from dataclasses import replace
from datetime import timedelta
import random
from uuid import uuid4

from ..domain.clock import from_epoch_ms, to_epoch_ms, local_day_bounds
from ..domain.commands import Command
from ..domain.models import Activity, DomainError, SleepSession, SleepSegment, require
from ..domain.segment import validate_segment


def apply_segment(engine, u, state, command, now):
    current = u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
    p = validate_segment(command.payload, engine.scheduler, current=current)
    deadline = now + timedelta(minutes=p['minutes'])
    if p['continue']:
        require(current is not None, 'no segment to continue')
        row = u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=?',
                                   (current.activity_id,)).fetchone()
        deadline = max(now, from_epoch_ms(row[0]) if row else now) + timedelta(minutes=p['minutes'])
        if state.active_sleep_session_id:
            session = u.sessions.get(state.active_sleep_session_id)
            deadline = min(deadline, session.hard_deadline_at)
            require(deadline > now, 'sleep hard deadline reached')
            u.sessions.update(replace(session, expected_wake_at=deadline, revision=session.revision+1))
        activity_id = current.activity_id
    else:
        resume = p.get('resumes_activity_id')
        previous = u.activities.get(resume) if resume else None
        require(not resume or (previous is not None and previous.character_id == state.character_id
                and previous.role == 'primary' and previous.status in ('completed', 'interrupted')
                and previous.sleep_session_id is None), 'not a resumable segment')
        name = previous.name if previous else p['event'].strip()
        sleeping = name == '睡眠'
        require(not previous or name == p['event'].strip(), 'resumed event must match history')
        require(not (state.fatigue is not None and state.fatigue >= 95 and p['energy_rate'] <= -0.05),
                'fatigue forbids demanding activity')
        require(not (state.energy is not None and state.energy <= 5 and p['energy_rate'] <= -0.05),
                'energy forbids demanding activity')
        require(not sleeping or p['energy_rate'] > 0, 'sleep must restore energy')
        # Keep the existing commitment protection at the command boundary.
        from .hybrid import bounds
        for commitment in u.commitments.for_character(state.character_id):
            if commitment.status in ('confirmed', 'needs_review'):
                left, right = bounds(commitment)
                require(deadline <= left or now >= right, 'segment conflicts with protected commitment')
        if state.active_sleep_session_id:
            require(not sleeping, 'continue the current sleep session')
            session = u.sessions.get(state.active_sleep_session_id)
            require(now <= session.hard_deadline_at, 'sleep recovery required')
            session = engine._close_piece(u, state, session, now, 'segment_changed', status='completed')
            u.sessions.update(replace(session, status='closed', ended_at=now, end_reason='segment_changed',
                                      interrupt_until=None, revision=session.revision+1))
            state = replace(state, active_sleep_session_id=None, primary_activity_id=None,
                            sleep_phase='awake', last_wake_at=now, awake_since_at=now)
        else:
            engine._close_activity(u, state, now, 'segment_changed', status='completed')
        activity_id = uuid4().hex
        session_id = None
        if sleeping:
            require(state.last_wake_at is None or now-state.last_wake_at >= timedelta(minutes=5), 'new-session cooldown')
            session_id = uuid4().hex
            u.sessions.add(SleepSession(session_id=session_id, character_id=state.character_id,
                started_at=now, expected_wake_at=deadline,
                hard_deadline_at=now+timedelta(minutes=engine.scheduler.segment_sleep_max_minutes),
                initial_expected_duration_minutes=p['minutes'], provenance=command.source, decision_id=command.decision_id))
            u.segments.add(SleepSegment(segment_id=uuid4().hex, character_id=state.character_id,
                session_id=session_id, started_at=now, kind='sleep', provenance=command.source))
        u.activities.add(Activity(activity_id=activity_id, character_id=state.character_id,
            name=name, started_at=now, provenance=command.source,
            category=previous.category if previous else 'rest' if p['energy_rate'] > 0 else 'other',
            visibility=previous.visibility if previous else 'public_summary',
            resumes_activity_id=resume, sleep_session_id=session_id,
            location=p['location'], emotion=p['emotion'], energy_rate=p['energy_rate']))
        state = replace(state, primary_activity_id=activity_id, secondary_activity_ids=(),
            sleep_phase='sleeping' if sleeping else 'awake', active_sleep_session_id=session_id,
            awake_since_at=None if sleeping else state.awake_since_at or now)
    u.connection.execute('INSERT INTO activity_checkpoints VALUES(?,?,0) ON CONFLICT(activity_id) '
        'DO UPDATE SET deadline_at_ms=excluded.deadline_at_ms,processed=0', (activity_id, to_epoch_ms(deadline)))
    return state


class SegmentService:
    def __init__(self, runtime, config):
        self.runtime, self.config = runtime, config

    async def _should_decide(self):
        engine = self.runtime.engine
        state = engine.state()
        if not state.primary_activity_id:
            return True, 'no_current_segment'
        with engine.database.read_view() as u:
            row = u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=?',
                                       (state.primary_activity_id,)).fetchone()
        if row is None or to_epoch_ms(engine.clock.now_utc()) >= row[0]:
            return True, 'segment_due'
        if (state.fatigue is not None and state.fatigue >= 80) or (state.energy is not None and state.energy <= 20):
            return True, 'body_threshold'
        if random.random()*100 < self.config.change_chance_percent:
            return True, 'chance'
        return False, 'continue'

    def context(self):
        engine = self.runtime.engine
        now = engine.clock.now_utc()
        state = engine.state()
        start, end = local_day_bounds(engine.clock.to_local(now, self.runtime.config.timezone).date(), self.runtime.config.timezone)
        with engine.database.read_view() as u:
            current = u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
            row = u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=?',
                                       (state.primary_activity_id,)).fetchone()
            # Model context uses the same public scope as the existing host port.
            history = [{'event': a.name, 'location': a.location, 'emotion': a.emotion,
                        'started_at': a.started_at.isoformat(), 'ended_at': a.ended_at.isoformat() if a.ended_at else None}
                       for a in sorted(u.activities.for_character(state.character_id), key=lambda a: a.started_at)
                       if a.visibility == 'public_summary' and a.started_at < end and (a.ended_at or now) >= start][-48:]
        return {'current': ({'event': current.name, 'location': current.location, 'emotion': current.emotion,
                    'elapsed_minutes': max(0, (now-current.started_at).total_seconds()/60),
                    'remaining_minutes': max(0, (from_epoch_ms(row[0])-now).total_seconds()/60) if row else None}
                    if current and current.visibility == 'public_summary' else None),
                'body': {key: getattr(state, key) for key in ('energy', 'fatigue', 'stress', 'sleep_debt_minutes')},
                'today_history': history,
                'limits': {'min_minutes': self.config.segment_min_minutes, 'max_minutes': self.config.segment_max_minutes,
                           'sleep_max_minutes': self.config.segment_sleep_max_minutes}}

    async def _generate_segment(self, context):
        # Coordinator reserves a durable attempt before RPC and validates/applies
        # advance_segment with a versioned basis, including failures and retries.
        return await self.runtime.coordinator.request('segment:'+context['reason'])

    def _continue(self, reason):
        engine = self.runtime.engine
        state = engine.state()
        p = {'event': '', 'location': '', 'emotion': '', 'minutes': self.config.decision_interval_minutes,
             'continue': True, 'energy_rate': 0.0}
        if state.primary_activity_id is None:
            # Empty bootstrap or deterministic wake with no budget: establish an
            # explicitly internal neutral activity instead of inventing a model decision.
            p.update(event='休息', **{'continue': False}, energy_rate=0.05)
        try:
            result = engine.execute(Command(idempotency_key='segment-fallback:'+uuid4().hex, kind='advance_segment',
                expected_state_version=state.state_version, payload=p))
        except DomainError:
            # A hard sleep deadline or protected commitment still wins over a
            # fallback. Leave recovery/the next window to reconcile it.
            return {'status': 'deferred', 'reason': reason}
        return {'status': 'continued', 'reason': reason, **result}

    async def tick(self):
        if not self.config.enabled:
            return {'status': 'disabled'}
        engine = self.runtime.engine
        now = engine.clock.now_utc()
        if now < engine.state().updated_at:
            return {'status': 'clock_rollback'}
        # Claim the decision window before awaiting; restarts/concurrent ticks
        # cannot roll the probability again or issue duplicate window calls.
        with engine.database.unit_of_work() as u:
            last = u.connection.execute("SELECT max(created_at_ms) FROM decision_triggers WHERE character_id='default' AND kind='segment_window'").fetchone()[0]
            if last is not None and to_epoch_ms(now)-last < self.config.decision_interval_minutes*60000:
                return {'status': 'not_due'}
            u.connection.execute('INSERT INTO decision_triggers VALUES(?,?,?,?,1)',
                                 ('segment-window:'+uuid4().hex, 'default', 'segment_window', to_epoch_ms(now)))
            u.commit()
        state = engine.state()
        if now > state.last_advanced_at:
            engine.execute(Command(idempotency_key='segment-body:'+uuid4().hex, kind='advance',
                expected_state_version=state.state_version, payload={'timezone': self.runtime.config.timezone,
                    'segment_energy_rates': True}))
        decide, reason = await self._should_decide()
        if not decide:
            return self._continue(reason)
        result = await self._generate_segment({'reason': reason})
        if result['status'] in ('budget_exhausted', 'invalid', 'uncertain', 'rejected'):
            return self._continue(result['status'])
        return result
