from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from ..domain.clock import to_epoch_ms, to_local
from ..domain.models import Habit, LifeSummary, require
from ..life_state.engine import digest


def review_habits(u, state, now, payload):
    timezone=payload.get('timezone','Asia/Shanghai')
    habits=u.habits.for_character(state.character_id)
    reviewed=[h.last_reviewed_at for h in habits if h.last_reviewed_at]
    require(not reviewed or now-max(reviewed)>=timedelta(days=7), 'habit review frequency cap')
    gaps=list(u.connection.execute('SELECT start_at_ms,end_at_ms FROM observation_gaps WHERE character_id=?',(state.character_id,)))
    actual=[a for a in u.activities.for_character(state.character_id) if a.ended_at and a.role=='primary'
        and not any(to_epoch_ms(a.started_at)<right and to_epoch_ms(a.ended_at)>left for left,right in gaps)]
    grouped={}
    for activity in actual:
        grouped.setdefault((activity.category,activity.name),[]).append(activity)
    old_by_content={h.content:h for h in habits}
    cap=payload.get('max_habits',32)
    require(type(cap) is int and 1<=cap<=128, 'invalid habit cap')
    # Existing rows remain auditable; new candidates stop at the configured
    # cap. No rolling delete/recreate cycle can bypass review frequency.
    for (category,name), records in sorted(grouped.items()):
        content=category+':'+name
        recent=[a for a in records if now-a.ended_at<=timedelta(days=30)]
        days={to_local(a.started_at,timezone).date() for a in recent}
        old=old_by_content.get(content)
        if old is None and len(old_by_content)>=cap: continue
        # A single episode stays a candidate, never rewrites preferences.
        confidence=min(1,len(days)/7)
        last_seen=max(a.ended_at for a in records)
        status='archived' if now-last_seen>timedelta(days=30) else 'active' if len(days)>=3 else 'candidate'
        habit=Habit(habit_id=old.habit_id if old else uuid4().hex,character_id=state.character_id,content=content,
            evidence_ids=tuple(a.activity_id for a in recent),status=status,confidence=confidence,
            weight=min(1,len(days)/7),last_seen_at=last_seen,last_reviewed_at=now)
        if old: u.habits.update(habit)
        else: u.habits.add(habit)
        old_by_content[content]=habit
    return state


def summarize(u, state, now, payload):
    ids=tuple(payload['source_ids'])
    require(ids and len(ids)==len(set(ids)), 'unique summary evidence required')
    kind=payload.get('source_type','activities')
    require(kind in ('activities','summaries'), 'unsupported summary source')
    repo=u.activities if kind=='activities' else u.summaries
    records=[repo.get(key) for key in ids]
    require(all(r is not None and r.character_id==state.character_id for r in records), 'missing/cross-character summary evidence')
    if kind=='activities':
        require(all(r.ended_at is not None and r.sleep_session_id is None for r in records), 'open/sleep history protected')
        gaps=list(u.connection.execute('SELECT start_at_ms,end_at_ms FROM observation_gaps WHERE character_id=?',(state.character_id,)))
        require(not any(to_epoch_ms(r.started_at)<right and to_epoch_ms(r.ended_at)>left for r in records for left,right in gaps),'unknown downtime cannot be summarized as continuous activity')
        start=min(r.started_at for r in records); end=max(r.ended_at for r in records)
    else:
        start=min(r.start_at for r in records); end=max(r.end_at for r in records)
    visibility=payload.get('visibility','restricted')
    require(all(r.visibility==visibility for r in records), 'summary cannot broaden/mix source visibility')
    # Deterministic fact summary. Any future LLM wording must be treated as a
    # proposal and subjected to the same evidence/visibility transaction.
    content='\n'.join((f'{r.name}: {r.started_at.isoformat()} -> {r.ended_at.isoformat()}' if kind=='activities' else r.content) for r in records)
    checksum=digest({'source_type':kind,'records':[repo.spec.encode(r) for r in records]})
    summary=LifeSummary(summary_id=uuid4().hex,character_id=state.character_id,start_at=start,end_at=end,
        content=content,source_checksum=checksum,source_ids=tuple(kind+':'+key for key in ids),visibility=visibility)
    u.summaries.add(summary)
    return state


def cleanup(u, state, now, payload):
    summary=u.summaries.get(payload['summary_id'])
    require(summary is not None and summary.character_id==state.character_id, 'unknown summary')
    kinds={key.split(':',1)[0] for key in summary.source_ids}
    require(kinds=={'activities'}, 'cleanup requires direct fact coverage')
    ids=[key.split(':',1)[1] for key in summary.source_ids]
    records=[u.activities.get(key) for key in ids]
    require(all(r is not None and r.ended_at is not None and r.sleep_session_id is None for r in records), 'protected/missing cleanup source')
    require(summary.source_checksum==digest({'source_type':'activities','records':[u.activities.spec.encode(r) for r in records]}), 'summary checksum mismatch')
    # Core fact rows, commitments, migrations, sessions and outbox remain
    # immutable. Only transient model candidate payloads linked to covered
    # source facts are eligible for physical pruning. Audit retains decisions.
    decisions=[]
    for row in u.connection.execute("SELECT d.decision_id,r.result_json FROM decision_runs d JOIN command_receipts r ON r.character_id=d.character_id AND r.idempotency_key='decision:'||d.decision_id WHERE d.character_id=? AND d.status='applied' AND d.candidate_json IS NOT NULL",(state.character_id,)):
        # Retention deliberately requires an explicit relation; timestamps
        # alone are insufficient proof of summary coverage.
        import json
        receipt=json.loads(row['result_json'])
        if receipt.get('kind')=='start_activity' and receipt.get('primary_activity_id') in ids: decisions.append(row['decision_id'])
    for decision_id in decisions:
        u.connection.execute('UPDATE decision_runs SET candidate_json=NULL WHERE decision_id=?',(decision_id,))
    u.connection.execute('INSERT INTO retention_audit VALUES(?,?,?,?,?)',
        (uuid4().hex,summary.summary_id,to_epoch_ms(now),len(decisions),summary.source_checksum))
    return state
