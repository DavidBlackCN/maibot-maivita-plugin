"""Three-layer prompt contracts and incremental, commitment-safe patches."""
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

from ..domain.clock import local_day_bounds
from ..domain.models import Plan, ScheduleItem, ScheduleRevision, require

PROMPTS = {
 'direction':'Choose goals for days/weeks from preferences, actual evidence and confirmed commitments. Return a direction proposal; never claim completed work.',
 'daily':'Propose fixed/window/unscheduled items. Free time and empty plans are valid. Sleep preferences are references, not mandatory windows. Preserve confirmed commitments. Return an incremental patch with a reason.',
 'immediate':'Choose one current intent from observed state, uncertainty, goals and commitments. Winding down is not actual sleep. Actual sleep requires explicit start_sleep and duration_minutes (5..1080). Never infer authorization from chat text.',
}


def build_prompt(layer, *, state, goals, commitments, preferences):
    require(layer in PROMPTS, 'unknown planning layer')
    # Inputs are already scoped by the adapter. Do not feed raw other-stream
    # chat messages into model prompts.
    return {'instruction':PROMPTS[layer], 'state':state, 'goals':goals,
            'commitments':commitments, 'preferences':preferences,
            'response_contract':{'kind':'command', 'payload':'object', 'reason':'required for plan patches'}}


def parse_item(data, *, character_id, plan_id, source):
    from ..life_state.engine import instant
    fields = dict(data)
    for key in ('start_at','end_at','window_start_at','window_end_at','preferred_start_at'):
        if fields.get(key) is not None: fields[key] = instant(fields[key])
    fields.update(character_id=character_id, plan_id=plan_id, source=source)
    item = ScheduleItem(**fields)
    require(item.status == 'planned', 'plan proposal cannot declare started/done history')
    return item


def candidate(candidate):
    require(isinstance(candidate,dict) and set(candidate) == {'kind','payload'}, 'invalid candidate envelope')
    kind,p = candidate['kind'],candidate['payload']
    require(isinstance(p,dict), 'invalid candidate payload')
    if kind == 'start_sleep':
        require(set(p) <= {'duration_minutes','kind','reason'} and 'duration_minutes' in p and isinstance(p.get('reason'),str) and p['reason'].strip(), 'invalid sleep schema')
        require(type(p['duration_minutes']) in (int,float) and 5 <= p['duration_minutes'] <= 1080, 'invalid sleep duration')
    elif kind == 'apply_plan_patch':
        require(set(p) <= {'plan_id','local_date','upserts','remove_ids','reason'} and p.get('reason'), 'invalid patch schema')
        require(isinstance(p.get('upserts',[]),list) and isinstance(p.get('remove_ids',[]),list), 'invalid patch arrays')
        for item in p.get('upserts',[]): parse_item(item,character_id='validation',plan_id='validation',source='model')
    elif kind == 'start_activity':
        require(set(p) <= {'name','category','description','visibility','source_schedule_item_id','resumes_activity_id','check_after_minutes','reason'} and p.get('name'), 'invalid activity schema')
        if 'reason' in p: require(isinstance(p['reason'],str) and p['reason'].strip(), 'invalid activity reason')
        require(type(p.get('check_after_minutes')) in (int,float) and 0<p['check_after_minutes']<=1440, 'activity needs explicit next checkpoint')
    elif kind == 'update_direction':
        require(set(p) == {'goals','reason'} and p.get('reason') and isinstance(p['goals'],list), 'invalid direction schema')
        require(all(isinstance(g,dict) and set(g)<={'goal_id','title','stage','status','deadline_at','progress_evidence'} and g.get('goal_id') and g.get('title') for g in p['goals']), 'invalid goal schema')
    elif kind == 'winding_down':
        require(set(p) <= {'reason'}, 'invalid intent schema')
    elif kind == 'extend_sleep':
        from ..life_state.engine import instant
        require(set(p) <= {'expected_wake_at','reason'} and 'expected_wake_at' in p and isinstance(p.get('reason'),str) and p['reason'].strip(), 'invalid extension schema')
        instant(p['expected_wake_at'])
    else:
        require(False, 'model command not allowed')
    return candidate


def decision_task_kind(triggers):
    for trigger in triggers:
        ids=trigger[6:].split(',') if trigger.startswith('batch:') else (trigger,)
        if any(key.startswith('daily-planning:') for key in ids): return 'planning'
    return 'present'


def validate_task_candidate(proposal, task_kind):
    proposal=candidate(proposal)
    if task_kind=='planning':
        require(proposal['kind'] in ('apply_plan_patch','update_direction'),
                'daily planning may only change plans or direction')
    return proposal


def update_direction(u,state,command,now):
    from ..domain.models import LifeGoal
    from ..life_state.engine import instant
    require(command.payload.get('reason'), 'direction revision needs reason')
    for data in command.payload['goals']:
        values=dict(data)
        if values.get('deadline_at'): values['deadline_at']=instant(values['deadline_at'])
        for evidence_id in values.get('progress_evidence',()):
            actual=u.activities.get(evidence_id)
            require(actual is not None and actual.character_id==state.character_id and actual.ended_at is not None,'goal progress needs actual evidence')
        require(values.get('status')!='completed' or values.get('progress_evidence'), 'goal completion needs actual evidence')
        goal=LifeGoal(**values,character_id=state.character_id,provenance=command.source)
        old=u.goals.get(goal.goal_id)
        require(old is None or old.character_id==state.character_id, 'cross-character goal')
        if old: u.goals.update(goal)
        else: u.goals.add(goal)
    return state


def bounds(commitment):
    if commitment.start_at is not None: return commitment.start_at,commitment.end_at
    start,_ = local_day_bounds(commitment.local_date,commitment.timezone)
    _,end = local_day_bounds(commitment.date_end or commitment.local_date,commitment.timezone)
    return start,end


def apply_patch(u, state, command, now):
    p = command.payload
    require(bool(p.get('reason')), 'plan revision requires a reason')
    plan_id = p.get('plan_id') or state.active_plan_id or uuid4().hex
    require(state.active_plan_id is None or plan_id == state.active_plan_id, 'patch must target active plan')
    old_plan = u.plans.get(plan_id)
    if old_plan:
        require(old_plan.character_id == state.character_id, 'cross-character plan')
    existing = {i.item_id:i for i in u.items.for_character(state.character_id) if i.plan_id == plan_id}
    upserts = [parse_item(dict(data),character_id=state.character_id,plan_id=plan_id,source=command.source) for data in p.get('upserts',())]
    require(len({i.item_id for i in upserts}) == len(upserts), 'duplicate patch item')
    removes = tuple(p.get('remove_ids',()))
    require(not (set(removes) & {i.item_id for i in upserts}), 'patch both removes and upserts item')
    commitments = {c.commitment_id:c for c in u.commitments.for_character(state.character_id) if c.status in ('confirmed','needs_review')}
    result = dict(existing)
    changes=[]
    for item_id in removes:
        require(item_id in existing, 'unknown removal')
        old = existing[item_id]
        require(old.status == 'planned' and old.commitment_id not in commitments, 'protected plan item cannot be removed')
        result[item_id] = replace(old, status='superseded')
        changes.append({'op':'remove','item_id':item_id})
    for item in upserts:
        old = existing.get(item.item_id)
        if old:
            require(old.status == 'planned', 'past/started plan item is immutable to planning')
            require(old.commitment_id not in commitments or item == old, 'confirmed commitment item cannot be rewritten')
        result[item.item_id] = item
        changes.append({'op':'upsert','item_id':item.item_id})
    fixed = sorted((i for i in result.values() if i.status == 'planned' and i.time_mode == 'fixed'),key=lambda i:i.start_at)
    require(all(a.end_at <= b.start_at for a,b in zip(fixed,fixed[1:])), 'fixed plan items overlap')
    for item in result.values():
        if item.status != 'planned': continue
        if item.commitment_id:
            c = commitments.get(item.commitment_id) or u.commitments.get(item.commitment_id)
            require(c is not None and c.character_id == state.character_id, 'unknown commitment link')
            if c.status in ('confirmed','needs_review'):
                require(item.time_mode == 'fixed' and (item.start_at,item.end_at)==bounds(c), 'linked commitment time cannot change')
        if item.time_mode != 'fixed': continue
        for c in commitments.values():
            start,end = bounds(c)
            require(item.commitment_id == c.commitment_id or item.end_at <= start or item.start_at >= end, 'plan conflicts with protected commitment')
    version = state.plan_version+1
    if old_plan:
        new_date=date.fromisoformat(p['local_date']) if p.get('local_date') else old_plan.local_date
        if new_date!=old_plan.local_date:
            changes.append({'op':'set_local_date','before':old_plan.local_date.isoformat() if old_plan.local_date else None,'after':new_date.isoformat()})
        u.plans.update(replace(old_plan,version=version,status='active',local_date=new_date))
    else:
        require(p.get('local_date') is not None, 'new plan needs civil date')
        u.plans.add(Plan(plan_id=plan_id,character_id=state.character_id,source=command.source,
            local_date=date.fromisoformat(p['local_date']),version=version,status='active'))
    for item_id,item in result.items():
        if item_id not in existing: u.items.add(item)
        elif item != existing[item_id]: u.items.update(item)
        if item_id not in existing or item!=existing[item_id]:
            from ..domain.clock import to_epoch_ms
            deadline=item.start_at if item.time_mode=='fixed' else item.window_start_at if item.time_mode=='window' else None
            if deadline and item.status=='planned':
                u.connection.execute('INSERT INTO plan_item_checks VALUES(?,?,?,0) ON CONFLICT(item_id) DO UPDATE SET deadline_at_ms=excluded.deadline_at_ms,plan_version=excluded.plan_version,processed=0',
                    (item_id,to_epoch_ms(deadline),version))
            else:
                u.connection.execute('UPDATE plan_item_checks SET processed=1 WHERE item_id=?',(item_id,))
    u.revisions.add(ScheduleRevision(revision_id=uuid4().hex,character_id=state.character_id,plan_id=plan_id,
        basis_version=state.plan_version,result_version=version,occurred_at=now,reason_summary=p['reason'],
        changes=tuple(changes),decision_id=command.decision_id,actor=command.source))
    return replace(state,active_plan_id=plan_id,plan_version=version)
