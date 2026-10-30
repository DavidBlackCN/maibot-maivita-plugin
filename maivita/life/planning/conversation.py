"""Neutral conversation opportunities; no message content or participant identity."""
import json
from uuid import uuid4

from ..domain.clock import to_epoch_ms
from ..domain.models import require

INVITATION_MS = 5 * 60_000
COOLDOWN_MS = 30 * 60_000
RESUME_MS = 4 * 60 * 60_000


def known(u, activity, now):
    return not u.connection.execute('SELECT 1 FROM observation_gaps WHERE character_id=? '
        'AND start_at_ms<? AND end_at_ms>? LIMIT 1',
        (activity.character_id,to_epoch_ms(activity.ended_at) if activity.ended_at else now,
         to_epoch_ms(activity.started_at))).fetchone()


def offer(engine):
    now = to_epoch_ms(engine.clock.now_utc())
    with engine.database.unit_of_work() as u:
        state = u.states.get('default')
        current = u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
        if (state.active_sleep_session_id or state.sleep_phase != 'awake' or not current
                or current.visibility != 'public_summary' or current.category == 'social' or not known(u,current,now)):
            return 'not_eligible'
        row = u.connection.execute('SELECT offered_at_ms FROM conversation_state WHERE character_id=?',
                                   (state.character_id,)).fetchone()
        if row and now - row[0] < COOLDOWN_MS:
            return 'cooldown'
        key = 'conversation:' + uuid4().hex
        u.connection.execute('INSERT INTO conversation_state '
            '(character_id,invitation_id,activity_id,offered_at_ms,expires_at_ms) VALUES(?,?,?,?,?) '
            'ON CONFLICT(character_id) DO UPDATE SET invitation_id=excluded.invitation_id,'
            'activity_id=excluded.activity_id,offered_at_ms=excluded.offered_at_ms,expires_at_ms=excluded.expires_at_ms',
            (state.character_id,key,current.activity_id,now,now+INVITATION_MS))
        u.connection.execute('INSERT INTO decision_triggers VALUES(?,?,?,?,0)',
                             (key,state.character_id,'conversation',now))
        u.commit()
    return 'offered'


def context(engine):
    now = to_epoch_ms(engine.clock.now_utc())
    state = engine.state()
    with engine.database.read_view() as u:
        row = u.connection.execute('SELECT * FROM conversation_state WHERE character_id=?',
                                   (state.character_id,)).fetchone()
        result = {'invitation': None, 'resume': None}
        if not row or state.active_sleep_session_id:
            return result
        if row['expires_at_ms'] > now and row['activity_id'] == state.primary_activity_id:
            result['invitation'] = {'id': row['invitation_id'], 'kind': 'explicit_chat_invitation'}
        if row['paused_activity_id'] and row['resume_until_ms'] > now:
            previous = u.activities.get(row['paused_activity_id'])
            if previous and previous.visibility == 'public_summary' and previous.status == 'interrupted' and known(u,previous,now):
                result['resume'] = {'activity_id': previous.activity_id, 'event': previous.name[:160],
                                    'location': previous.location[:160], 'emotion': previous.emotion[:160]}
        return result


def expire(runtime, *, restart=False):
    """Drop expired/restarted invitations before selecting the durable batch."""
    now = to_epoch_ms(runtime.engine.clock.now_utc())
    with runtime.database.unit_of_work() as u:
        u.connection.execute("UPDATE decision_triggers SET consumed=1 WHERE kind='conversation' AND "
            "( ? OR created_at_ms<=? OR trigger_id NOT IN (SELECT invitation_id FROM conversation_state "
            "WHERE activity_id=(SELECT primary_activity_id FROM life_state WHERE character_id='default'))) ",
            (int(restart),now-INVITATION_MS))
        if restart:
            u.connection.execute('UPDATE conversation_state SET invitation_id=NULL,expires_at_ms=0')
        u.commit()


def apply(u, state, command, current, payload, now):
    """Called inside the activity transition transaction, after all validation."""
    stamp = to_epoch_ms(now)
    row = u.connection.execute('SELECT * FROM conversation_state WHERE character_id=?',
                               (state.character_id,)).fetchone()
    token = payload.get('pause_for_chat')
    resume = payload.get('resumes_activity_id')
    if token:
        require(row is not None and row['invitation_id'] == token and row['expires_at_ms'] > stamp
                and current is not None and row['activity_id'] == current.activity_id
                and current.visibility == 'public_summary' and known(u,current,stamp), 'chat invitation expired or changed')
        decision = u.connection.execute('SELECT trigger_ids_json FROM decision_runs WHERE decision_id=?',
                                        (command.decision_id,)).fetchone()
        triggers = [key for batch in json.loads(decision[0]) for key in batch.removeprefix('batch:').split(',')] if decision else []
        require(token in triggers, 'chat pause requires its claimed decision')
        # A nested interruption never builds a backlog or replaces the original.
        if not row['paused_activity_id'] or row['resume_until_ms'] <= stamp:
            u.connection.execute('UPDATE conversation_state SET paused_activity_id=?,resume_until_ms=? WHERE character_id=?',
                                 (current.activity_id,stamp+RESUME_MS,state.character_id))
        u.connection.execute('UPDATE conversation_state SET invitation_id=NULL WHERE character_id=?',(state.character_id,))
    if resume:
        require(row is not None and row['paused_activity_id'] == resume and row['resume_until_ms'] > stamp,
                'not the retained interrupted activity')
        require(known(u,u.activities.get(resume),stamp), 'interrupted activity has unknown continuity')
    if resume:
        u.connection.execute('UPDATE conversation_state SET paused_activity_id=NULL,resume_until_ms=NULL WHERE character_id=?',
                             (state.character_id,))
