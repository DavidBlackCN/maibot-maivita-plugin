"""Deterministic staging import from a verified offline snapshot only.

No activation or source writes. Every goal row is retained, including malformed
JSON. Source identity is operator-supplied and stable across snapshot hashes.
"""
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from uuid import uuid4

from ..domain.clock import as_utc, resolve_local, to_epoch_ms, to_local
from ..domain.models import Commitment, LifeGoal, Plan, ScheduleItem, json_text, require
from ..life_state.engine import digest
from .safety import fingerprint, inspect_database, readonly


def parse_legacy_time(value, timezone):
    parsed=datetime.fromisoformat(value)
    return as_utc(parsed) if parsed.tzinfo else resolve_local(parsed,timezone)


def classify(row, *, timezone, today, source_id):
    stable=digest({'database':source_id,'table':'goals','key':row['goal_id']})
    try:
        p=json.loads(row['parameters'] or '{}'); c=json.loads(row['conditions'] or '{}')
        require(isinstance(p,dict) and isinstance(c,dict), 'JSON object required')
        require(row['status'] in ('active','paused','completed','cancelled'), 'unknown legacy status')
        if row['goal_type']=='pending_commitment':
            day=date.fromisoformat(p['commitment_date'])
            status='cancelled' if row['status']=='cancelled' else 'confirmed' if row['status']=='active' and day>=today else 'needs_review'
            # A time without duration is a point, not a safe activity interval.
            # Keep it as a protected date-level record pending human resolution.
            if p.get('time'): status='needs_review'
            commitment=Commitment(commitment_id='legacy-c-'+stable,character_id='default',title=row['name'],
                source_stream_id=row['chat_id'],provenance='legacy_import',timezone=timezone,local_date=day,status=status,
                visibility='source_stream' if row['chat_id']!='global' else 'restricted',
                confirmation_basis='legacy_active_acceptance' if status=='confirmed' else None,
                notes=json_text({'legacy_status':row['status'],'parameters':p}))
            return 'commitment', [('commitments',commitment)], 'ambiguous_commitment' if status=='needs_review' else None
        if p.get('is_commitment'):
            return 'needs_review',[], 'unproven_commitment_link'
        window=p.get('time_window',c.get('time_window'))
        if 'time_window' in p and 'time_window' in c:
            require(p['time_window']==c['time_window'], 'conflicting_time_windows')
        if window is not None:
            require(isinstance(window,list) and len(window)==2 and all(type(v) is int for v in window), 'invalid minute array')
            start,end=window
            require(0<=start<1440 and start<end<=2880 and end-start<=1440, 'invalid legacy interval')
            created=parse_legacy_time(row['created_at'],timezone)
            day=date.fromisoformat(p['date']) if p.get('date') else to_local(created,timezone).date()
            midnight=datetime.combine(day,time())
            start_at=resolve_local(midnight+timedelta(minutes=start),timezone)
            end_at=resolve_local(midnight+timedelta(minutes=end),timezone)
            plan=Plan(plan_id='legacy-p-'+stable,character_id='default',source='legacy_import',local_date=day)
            item=ScheduleItem(item_id='legacy-i-'+stable,character_id='default',plan_id=plan.plan_id,name=row['name'],
                source='legacy_import',time_mode='fixed',start_at=start_at,end_at=end_at,
                status='planned' if row['status']=='active' else 'superseded')
            return 'schedule',[('plans',plan),('items',item)],None
        if row['goal_type'] in ('study','work','health','personal','creative','long_term','health_check','social_maintenance','learn_topic','custom'):
            goal=LifeGoal(goal_id='legacy-g-'+stable,character_id='default',title=row['name'],provenance='legacy_import',
                status=row['status'],progress_evidence=('legacy_status:'+row['status'],))
            return 'goal',[('goals',goal)],None
        return 'needs_review',[],'unknown_goal_type'
    except (ValueError,TypeError,KeyError) as exc:
        return 'needs_review',[],type(exc).__name__


def import_snapshot(database, snapshot: Path, *, source_database_id, timezone='Asia/Shanghai', fault=None):
    require(bool(source_database_id), 'stable source database identity required')
    snapshot=Path(snapshot)
    require(snapshot.is_absolute() and snapshot.resolve()!=database.path, 'explicit distinct source snapshot required')
    require(inspect_database(snapshot,offline=True)['valid_legacy_schema'], 'unverified legacy schema')
    with closing(readonly(snapshot,offline=True)) as src:
        src.row_factory=__import__('sqlite3').Row
        rows=[dict(row) for row in src.execute('SELECT * FROM goals ORDER BY goal_id')]
    fault=fault or (lambda point:None)
    run_id=uuid4().hex; counts=Counter(); reviewed=[]; reused=0
    now=database.clock.now_utc()
    with database.unit_of_work() as u:
        require(u.states.get('default') is not None, 'initialize unknown state before import')
        prior={row['source_primary_key'] for row in u.connection.execute('SELECT source_primary_key FROM legacy_import_records WHERE source_database_id=?',(source_database_id,))}
        require(prior <= {row['goal_id'] for row in rows}, 'source rows disappeared; explicit reconciliation required')
        u.connection.execute('INSERT INTO migration_runs VALUES(?,?,?,?,?,?,?)',
            (run_id,source_database_id,fingerprint(snapshot),'staged',to_epoch_ms(now),None,'{}'))
        for row in rows:
            raw=json_text(row); row_hash=digest(row)
            previous=u.connection.execute('SELECT * FROM legacy_import_records WHERE source_database_id=? AND source_table=? AND source_primary_key=?',(source_database_id,'goals',row['goal_id'])).fetchone()
            if previous:
                require(previous['source_row_hash']==row_hash, 'source row changed: '+row['goal_id'])
                counts[previous['classification']]+=1; reused+=1
                if previous['review_reason']: reviewed.append(row['goal_id'])
                continue
            classification,entities,reason=classify(row,timezone=timezone,today=to_local(now,timezone).date(),source_id=source_database_id)
            record_id=digest({'source':source_database_id,'key':row['goal_id']})
            u.connection.execute('INSERT INTO legacy_import_records VALUES(?,?,?,?,?,?,?,?,?)',
                (record_id,run_id,source_database_id,'goals',row['goal_id'],row_hash,raw,classification,reason))
            for repo_name,entity in entities:
                repo=getattr(u,repo_name); repo.add(entity)
                u.connection.execute('INSERT INTO legacy_id_map VALUES(?,?,?)',(record_id,repo_name,getattr(entity,repo.spec.key)))
            counts[classification]+=1
            if reason: reviewed.append(row['goal_id'])
            fault('after_row')
        require(sum(counts.values())==len(rows), 'row conservation failed')
        report={'source_rows':len(rows),'counts':dict(counts),'review_ids':reviewed,'reused':reused,'activated':False,
                'migration_run_id':run_id,'state_history_imported':False}
        u.connection.execute('UPDATE migration_runs SET status=?,completed_at_ms=?,report_json=? WHERE migration_run_id=?',
            ('staged' if reviewed else 'validated',to_epoch_ms(now),json_text(report),run_id))
        u.commit()
    return report
