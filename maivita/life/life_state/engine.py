"""Single transaction command application; no model calls or network effects.

This is an internal application API. Public callers use adapters which supply
trusted context; a JSON field named source/owner is never an authorization.
"""
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
import hashlib
import json
from uuid import uuid4

from ..domain.clock import as_utc, iso_instant, to_epoch_ms
from ..domain.commands import Command
from ..domain.constraints import observed_sleep_minutes
from ..domain.models import (
    Activity, ActivityStatus, Commitment, DomainError, LifeEvent, LifeState, Plan,
    ScheduleItem, ScheduleRevision, SleepSegment, SleepSession, json_text, require,
)


def digest(value):
    return hashlib.sha256(json_text(value).encode('utf-8')).hexdigest()


def instant(value):
    return as_utc(datetime.fromisoformat(value)) if isinstance(value, str) else as_utc(value)


def command_data(command):
    data = {f.name: getattr(command, f.name) for f in fields(command)}
    if data['expires_at'] is not None:
        data['expires_at'] = iso_instant(data['expires_at'])
    return data


class LifeStateEngine:
    def __init__(self, database, *, fault=None, scheduler=None):
        from ..domain.segment import SchedulerConfig
        self.scheduler = scheduler or SchedulerConfig()
        self.database = database
        self.clock = database.clock
        self.fault = fault or (lambda point: None)

    def initialize(self, character_id='default'):
        with self.database.unit_of_work() as u:
            state = u.states.get(character_id)
            if state is None:
                now = self.clock.now_utc()
                state = LifeState(character_id=character_id, updated_at=now, last_advanced_at=now)
                u.states.add(state)
                u.commit()
            return state

    def state(self, character_id='default'):
        with self.database.read_view() as u:
            state = u.states.get(character_id)
            require(state is not None, 'character not initialized')
            return state

    def execute(self, command: Command):
        payload_hash = digest(command_data(command))
        with self.database.unit_of_work() as u:
            row = u.connection.execute('SELECT payload_hash,result_json FROM command_receipts WHERE character_id=? AND idempotency_key=?',
                                       (command.character_id, command.idempotency_key)).fetchone()
            if row:
                require(row['payload_hash'] == payload_hash, 'idempotency key reused with different payload')
                return json.loads(row['result_json'])
            before = u.states.get(command.character_id)
            require(before is not None, 'character not initialized')
            require(before.state_version == command.expected_state_version, 'stale state basis')
            for key in ('plan_version', 'config_revision'):
                expected = getattr(command, 'expected_' + key)
                require(expected is None or expected == getattr(before, key), 'stale ' + key + ' basis')
            now = self.clock.now_utc()
            require(now >= before.updated_at, 'wall clock rollback: reconciliation required')
            require(command.expires_at is None or now < command.expires_at, 'command expired')
            if command.source == 'model':
                run = u.connection.execute('SELECT * FROM decision_runs WHERE decision_id=?', (command.decision_id,)).fetchone()
                require(run is not None and run['character_id'] == command.character_id and run['status'] == 'validated', 'unverified model decision')
                require((run['basis_state_version'], run['basis_plan_version'], run['basis_config_revision']) ==
                        (before.state_version, before.plan_version, before.config_revision), 'stale model basis')
                require(run['candidate_hash'] == digest({'kind': command.kind, 'payload': command.payload}), 'model candidate changed')
            after, event_type = self._apply(u, replace(before,updated_at=now), command, now)
            after = replace(after, state_version=before.state_version + 1, updated_at=now)
            u.states.update(after, expected_version=before.state_version)
            self.fault('after_state')
            # Events expose a public summary only; private names/reasons remain
            # in scoped records. Consumers fetch details through query policy.
            event_id = uuid4().hex
            occurred_at=after.last_wake_at if command.kind=='wake' else now
            u.events.add(LifeEvent(event_id=event_id, character_id=after.character_id,
                event_type=event_type, state_version=after.state_version, event_index=0,
                occurred_at=occurred_at, recorded_at=now, payload={'sleep_phase': after.sleep_phase.value},
                source=command.source, causation_id=command.idempotency_key,
                correlation_id=command.decision_id, expires_at=occurred_at + timedelta(minutes=30)))
            if command.kind in ('wake','check_activity','check_plan_item','propose_commitment','enqueue_replan','recover_observation_gap'):
                u.connection.execute('INSERT INTO decision_triggers VALUES(?,?,?,?,0)',
                    (event_id,after.character_id,command.kind,to_epoch_ms(now)))
            result = {'state_version': after.state_version, 'event_id': event_id, 'kind': command.kind,
                      'primary_activity_id': after.primary_activity_id}
            u.connection.execute('INSERT INTO command_receipts VALUES (?,?,?,?,?,?)',
                (after.character_id, command.idempotency_key, payload_hash, json_text(result), after.state_version, to_epoch_ms(now)))
            if command.source == 'model':
                u.connection.execute("UPDATE decision_runs SET status='applied' WHERE decision_id=?", (command.decision_id,))
            self.fault('before_commit')
            u.commit()
        self.fault('after_commit')
        return result

    def _close_activity(self, u, state, at, reason, status='interrupted'):
        for activity_id in state.secondary_activity_ids:
            secondary = u.activities.get(activity_id)
            require(at > secondary.started_at, 'zero-length secondary transition')
            u.activities.update(replace(secondary,ended_at=at,status='interrupted',end_reason=reason,interruption_reason=reason))
        if state.primary_activity_id is not None:
            actual = u.activities.get(state.primary_activity_id)
            require(at > actual.started_at, 'transition would create a zero-length activity')
            u.activities.update(replace(actual, ended_at=at, status=status, end_reason=reason,
                                        interruption_reason=reason if status == 'interrupted' else None))

    def _sleep_piece(self, u, state, session, at, kind, provenance):
        activity_id = uuid4().hex
        u.activities.add(Activity(activity_id=activity_id, character_id=state.character_id,
            name='睡眠' if kind == 'sleep' else '短暂清醒', category='rest', started_at=at,
            provenance=provenance, sleep_session_id=session.session_id, visibility='public_summary'))
        u.segments.add(SleepSegment(segment_id=uuid4().hex, character_id=state.character_id,
            session_id=session.session_id, started_at=at, kind=kind, provenance=provenance))
        return replace(state, primary_activity_id=activity_id)

    def _close_piece(self, u, state, session, at, reason, status='interrupted'):
        self._close_activity(u, state, at, reason, status=status)
        opened = [s for s in u.segments.for_character(state.character_id) if s.session_id == session.session_id and s.ended_at is None]
        require(len(opened) == 1, 'missing open sleep segment')
        segment = opened[0]
        require(at > segment.started_at, 'transition would create a zero-length segment')
        u.segments.update(replace(segment, ended_at=at))
        # The cache stores whole minutes. Recompute from all precise segment
        # timestamps so repeated short interruptions cannot accumulate rounding.
        minutes = observed_sleep_minutes(s for s in u.segments.for_character(state.character_id)
                                         if s.session_id == session.session_id)
        return replace(session, actual_sleep_minutes=minutes)

    def _apply(self, u, state, command, now):
        p, kind = command.payload, command.kind
        if kind == 'advance_segment':
            from ..planning.segments import apply_segment
            return apply_segment(self, u, state, command, now), 'life.activity.segment_advanced'
        if kind == 'recover_observation_gap':
            require(command.source=='internal','recovery is internal only')
            start=instant(p['start_at'])
            require(start<now,'invalid recovery gap')
            u.connection.execute('INSERT INTO observation_gaps VALUES(?,?,?,?,?)',
                (uuid4().hex,state.character_id,to_epoch_ms(start),to_epoch_ms(now),'process_downtime'))
            return replace(state,confidence='unknown',degraded_reason='recovered_unknown_gap'),'life.state.recovered'
        if kind == 'start_activity':
            require(state.active_sleep_session_id is None, 'sleep session must end before activity')
            item_id = p.get('source_schedule_item_id')
            if item_id:
                item=u.items.get(item_id)
                require(item is not None and item.character_id==state.character_id and item.plan_id==state.active_plan_id and item.status=='planned', 'invalid source plan item')
                if item.time_mode=='fixed': require(item.start_at<=now<item.end_at, 'fixed activity not due')
                elif item.time_mode=='window':
                    require(item.window_start_at<=now and now+timedelta(minutes=item.preferred_duration_minutes)<=item.window_end_at, 'window must be materialized within bounds')
            from ..planning.hybrid import bounds
            for c in u.commitments.for_character(state.character_id):
                if c.status in ('confirmed','needs_review'):
                    start,end=bounds(c)
                    require(not start<=now<end or (item_id and item.commitment_id==c.commitment_id), 'activity conflicts with protected commitment')
            self._close_activity(u, state, now, 'replaced')
            resume = p.get('resumes_activity_id')
            previous = u.activities.get(resume) if resume else None
            require(not resume or (previous is not None and previous.character_id == state.character_id and previous.status == 'interrupted' and previous.role=='primary'), 'not a resumable activity')
            activity_id = uuid4().hex
            u.activities.add(Activity(activity_id=activity_id, character_id=state.character_id,
                name=previous.name if previous else p['name'], category=previous.category if previous else p.get('category', 'other'),
                description=previous.description if previous else p.get('description', ''), started_at=now,
                provenance=command.source, visibility=previous.visibility if previous else p.get('visibility','restricted'),
                resumes_activity_id=resume, source_schedule_item_id=p.get('source_schedule_item_id')))
            minutes=p.get('check_after_minutes',45)
            require(type(minutes) in (int,float) and 0<minutes<=1440, 'invalid activity checkpoint')
            deadline=now+timedelta(minutes=minutes)
            if item_id and item.time_mode=='fixed': deadline=min(deadline,item.end_at)
            if item_id and item.time_mode=='window': deadline=min(deadline,item.window_end_at)
            u.connection.execute('INSERT INTO activity_checkpoints VALUES(?,?,0)',(activity_id,to_epoch_ms(deadline)))
            return replace(state, primary_activity_id=activity_id, secondary_activity_ids=(), sleep_phase='awake', awake_since_at=state.awake_since_at or now), 'life.activity.started'
        if kind == 'check_activity':
            require(command.source=='internal' and state.primary_activity_id==p['activity_id'] and state.active_sleep_session_id is None, 'checkpoint activity changed')
            row=u.connection.execute('SELECT * FROM activity_checkpoints WHERE activity_id=?',(p['activity_id'],)).fetchone()
            require(row is not None and not row['processed'] and to_epoch_ms(now)>=row['deadline_at_ms'], 'checkpoint not due')
            u.connection.execute('UPDATE activity_checkpoints SET processed=1 WHERE activity_id=?',(p['activity_id'],))
            return state,'life.activity.check_due'
        if kind == 'check_plan_item':
            require(command.source=='internal' and state.active_sleep_session_id is None,'plan check cannot wake sleep')
            item=u.items.get(p['item_id'])
            row=u.connection.execute('SELECT * FROM plan_item_checks WHERE item_id=?',(p['item_id'],)).fetchone()
            require(item is not None and item.plan_id==state.active_plan_id and item.status=='planned' and row is not None
                and not row['processed'] and to_epoch_ms(now)>=row['deadline_at_ms'], 'plan item not due')
            u.connection.execute('UPDATE plan_item_checks SET processed=1 WHERE item_id=?',(item.item_id,))
            return state,'life.plan.item_due'
        if kind == 'end_activity':
            require(state.active_sleep_session_id is None and state.primary_activity_id is not None, 'no awake activity to end')
            self._close_activity(u, state, now, p.get('reason', 'explicit_end'), p.get('status', 'completed'))
            return replace(state, primary_activity_id=None, secondary_activity_ids=()), 'life.activity.ended'
        if kind == 'start_secondary':
            require(state.active_sleep_session_id is None and state.primary_activity_id is not None and len(state.secondary_activity_ids)<2, 'secondary activity limit or incompatible state')
            primary=u.activities.get(state.primary_activity_id)
            # Conservative compatibility matrix: no parallel work/study or
            # travel; a meal/rest may accompany quiet entertainment.
            allowed={('daily_life','entertainment'),('entertainment','daily_life'),('rest','entertainment')}
            require((primary.category,p.get('category')) in allowed, 'incompatible secondary activity')
            activity_id=uuid4().hex
            u.activities.add(Activity(activity_id=activity_id,character_id=state.character_id,name=p['name'],
                category=p['category'],role='secondary',started_at=now,provenance=command.source,visibility=p.get('visibility','restricted')))
            return replace(state,secondary_activity_ids=state.secondary_activity_ids+(activity_id,)), 'life.activity.secondary_started'
        if kind == 'end_secondary':
            activity_id=p['activity_id']
            require(activity_id in state.secondary_activity_ids, 'secondary not active')
            actual=u.activities.get(activity_id)
            u.activities.update(replace(actual,status='completed',ended_at=now,end_reason='explicit_end'))
            return replace(state,secondary_activity_ids=tuple(i for i in state.secondary_activity_ids if i!=activity_id)), 'life.activity.secondary_ended'
        if kind == 'winding_down':
            require(state.active_sleep_session_id is None, 'already sleeping')
            return replace(state, sleep_phase='winding_down'), 'life.sleep.intent'
        if kind == 'start_sleep':
            require(command.source in ('model','authorized','legacy_observed'), 'sleep requires a validated model decision or local authorization')
            require(state.active_sleep_session_id is None, 'sleep session already open')
            duration = p['duration_minutes']
            require(type(duration) in (int,float) and 0 < duration < 100000, 'invalid sleep duration')
            autonomous = command.source in ('model','authorized')
            if autonomous:
                require(5 <= duration <= 1080, 'sleep duration outside engineering limits')
                require(state.last_wake_at is None or now - state.last_wake_at >= timedelta(minutes=5), 'new-session cooldown')
                from ..planning.hybrid import bounds
                for c in u.commitments.for_character(state.character_id):
                    if c.status in ('confirmed','needs_review'):
                        start,end=bounds(c)
                        require(now+timedelta(minutes=duration)<=start or now>=end, 'sleep conflicts with protected commitment')
            self._close_activity(u, state, now, 'sleep_started')
            session = SleepSession(session_id=uuid4().hex, character_id=state.character_id,
                started_at=now, expected_wake_at=now + timedelta(minutes=duration),
                hard_deadline_at=now + timedelta(minutes=1080 if autonomous else duration),
                initial_expected_duration_minutes=duration, provenance=command.source, decision_id=command.decision_id,
                kind=p.get('kind','night_sleep'), reason_summary=p.get('reason',''))
            u.sessions.add(session)
            state = self._sleep_piece(u, state, session, now, 'sleep', command.source)
            return replace(state, sleep_phase='sleeping', secondary_activity_ids=(), active_sleep_session_id=session.session_id, awake_since_at=None), 'life.sleep.started'
        if kind in ('wake', 'interrupt_sleep', 'resume_sleep', 'extend_sleep'):
            session = u.sessions.get(state.active_sleep_session_id) if state.active_sleep_session_id else None
            require(session is not None, 'no open sleep session')
            if kind == 'extend_sleep':
                require(command.source == 'model', 'sleep extension requires validated model decision')
                wake = instant(p['expected_wake_at'])
                require(session.extension_count < 2 and now < session.expected_wake_at < wake <= session.hard_deadline_at, 'sleep extension limit')
                u.sessions.update(replace(session, expected_wake_at=wake, extension_count=session.extension_count+1, revision=session.revision+1))
                return state, 'life.sleep.extended'
            if kind == 'wake':
                require(command.source == 'authorized' or (command.source == 'internal' and now >= min(session.expected_wake_at, session.hard_deadline_at)) or command.source == 'legacy_observed', 'wake not authorized or due')
                # A missed deadline closes only the persisted sleep segment at
                # its deadline; no offline awake activity is fabricated.
                at = min(now, session.expected_wake_at, session.hard_deadline_at) if command.source == 'internal' else min(now, session.hard_deadline_at)
                piece_end = min(at,session.interrupt_until) if session.status=='interrupted' else at
                session = self._close_piece(u, state, session, piece_end, p.get('reason','wake'))
                u.sessions.update(replace(session, status='closed', ended_at=at, end_reason=p.get('reason','deadline'), interrupt_until=None,
                    recovery_marker='recovered_after_downtime' if now>at else session.recovery_marker,revision=session.revision+1))
                return replace(state, sleep_phase='awake', primary_activity_id=None, active_sleep_session_id=None, last_wake_at=at, awake_since_at=at), 'life.wakeup'
            if kind == 'interrupt_sleep':
                require(command.source == 'authorized' and session.status == 'active', 'interruption not authorized')
                minutes = p.get('minutes', 10)
                require(type(minutes) in (int,float) and 0 < minutes <= 10 and now < session.expected_wake_at, 'invalid interruption')
                until = min(now + timedelta(minutes=minutes), session.expected_wake_at, session.hard_deadline_at)
                session = self._close_piece(u, state, session, now, 'authorized_interrupt')
                session = replace(session, status='interrupted', interrupt_until=until, revision=session.revision+1)
                u.sessions.update(session)
                state = self._sleep_piece(u, state, session, now, 'awake_interrupt', command.source)
                return replace(state, sleep_phase='temporarily_awake', awake_since_at=now), 'life.sleep.interrupted'
            require(command.source == 'internal' and session.status == 'interrupted' and now >= session.interrupt_until, 'interruption not due')
            require(now < session.expected_wake_at, 'wake deadline takes precedence')
            # After a long outage, unknown time beyond the interruption expiry
            # remains a gap. Resume at observation, never backfill new sleep.
            session = self._close_piece(u, state, session, session.interrupt_until, 'interrupt_expired')
            session = replace(session, status='active', interrupt_until=None, revision=session.revision+1)
            u.sessions.update(session)
            state = self._sleep_piece(u, state, session, now, 'sleep', 'internal')
            return replace(state, sleep_phase='sleeping', awake_since_at=None), 'life.sleep.resumed'
        if kind == 'apply_plan_patch':
            from ..planning.hybrid import apply_patch
            return apply_patch(u, state, command, now), 'life.plan.revised'
        if kind == 'update_direction':
            from ..planning.hybrid import update_direction
            return update_direction(u,state,command,now), 'life.direction.revised'
        if kind == 'manage_goal':
            require(command.source=='authorized','goal management requires policy')
            from ..domain.models import LifeGoal
            require(isinstance(p.get('goal_id'),str) and p['goal_id'].strip(),'goal ID required')
            require(p.get('action')!='create' or isinstance(p.get('title'),str) and p['title'].strip(),'goal title required')
            old=u.goals.get(p['goal_id'])
            action=p['action']
            require(action in ('create','update','pause','resume','complete','cancel','delete'),'unknown goal action')
            if action=='create':
                require(old is None,'goal already exists')
                u.goals.add(LifeGoal(goal_id=p['goal_id'],character_id=state.character_id,title=p['title'],
                    provenance=command.source,stage=p.get('stage',''),visibility=p.get('visibility','restricted')))
            else:
                require(old is not None and old.character_id==state.character_id,'goal not found')
                changes={}
                if action=='update':
                    for key in ('title','stage','deadline_at'):
                        if key in p: changes[key]=instant(p[key]) if key=='deadline_at' and p[key] else p[key]
                elif action=='complete':
                    evidence=tuple(p.get('progress_evidence',()))
                    require(evidence,'goal completion needs actual evidence')
                    for key in evidence:
                        actual=u.activities.get(key)
                        require(actual is not None and actual.character_id==state.character_id and actual.ended_at is not None,'invalid goal evidence')
                    changes.update(status='completed',progress_evidence=old.progress_evidence+evidence)
                else:
                    changes['status']={'pause':'paused','resume':'active','cancel':'cancelled','delete':'cancelled'}[action]
                u.goals.update(replace(old,**changes))
            return state,'life.goal.changed'
        if kind == 'propose_commitment':
            data = dict(p)
            for name in ('start_at','end_at'):
                if data.get(name) is not None: data[name] = instant(data[name])
            for name in ('local_date','date_end'):
                if data.get(name) is not None: data[name] = date.fromisoformat(data[name])
            data.update(commitment_id=uuid4().hex, character_id=state.character_id, provenance=command.source, status='proposed')
            u.commitments.add(Commitment(**data))
            return state, 'life.commitment.proposed'
        if kind == 'enqueue_replan':
            require(command.source=='authorized' and p.get('reason'), 'replan request requires policy and reason')
            return state, 'life.replan.requested'
        if kind == 'advance':
            from .subjective import advance
            return advance(u, state, now, p), 'life.estimates.updated'
        if kind in ('review_habits','summarize','cleanup'):
            require(command.source=='internal', 'continuity operations are internal only')
            from ..continuity import service
            return getattr(service,kind)(u,state,now,p), 'life.continuity.'+kind
        if kind == 'configure':
            require(command.source == 'authorized', 'configuration requires authorization')
            require(state.active_sleep_session_id is None, 'mode/config change requires explicit session handoff')
            return replace(state, config_revision=state.config_revision+1), 'life.configuration.changed'
        raise DomainError('unsupported command: ' + kind)
