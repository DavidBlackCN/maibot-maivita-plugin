"""Detailed status for the explicit status command only; never a prompt/API feed."""
import asyncio
import json
import math
from collections import Counter

from ..life.domain.clock import from_epoch_ms, local_day_bounds, to_epoch_ms, to_local
from ..life.life_state.projections import QueryScope, visible
from ..life.planning.hybrid import decision_task_kind
from .presentation import clean

HUSH_API='davidblackcn.maibot-maihush-plugin.get_public_constraints'
PHASES={'awake':'清醒','winding_down':'准备睡觉','sleeping':'睡眠中',
        'temporarily_awake':'临时醒来','waking':'正在醒来'}


def snapshot(runtime, timezone, stream_id):
    now=runtime.engine.clock.now_utc()
    today=to_local(now,timezone).date()
    start,end=local_day_bounds(today,timezone)
    scope=QueryScope(stream_id=stream_id)
    with runtime.database.read_view() as u:
        state=u.states.get('default')
        activity=u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
        if activity is not None and not visible(activity,scope): activity=None
        checkpoint=u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=?',
            (activity.activity_id,)).fetchone() if activity else None
        plans={p.plan_id:p for p in u.plans.for_character(state.character_id)}
        counts=Counter()
        for item in u.items.for_character(state.character_id):
            if not visible(item,scope) or item.status not in ('done','started','planned'): continue
            item_start=item.start_at or item.window_start_at
            item_end=item.end_at or item.window_end_at
            on_day=(item_start<end and item_end>start if item_start else
                    plans[item.plan_id].local_date==today)
            if on_day: counts[item.status]+=1
        runs=list(u.connection.execute('SELECT * FROM decision_runs WHERE character_id=? ORDER BY started_at_ms DESC,rowid DESC',
                                       (state.character_id,)))
    used=sum(to_epoch_ms(start)<=r['started_at_ms']<to_epoch_ms(end) for r in runs)
    planning=next((r for r in runs if decision_task_kind(json.loads(r['trigger_ids_json']))=='planning'),None)
    latest=runs[0] if runs else None
    degraded=bool(latest and (latest['diagnostic'] or '').startswith('degraded:'))
    return {'phase':state.sleep_phase,
            'wake':to_local(state.last_wake_at,timezone).strftime('%m-%d %H:%M') if state.last_wake_at else '未知',
            'activity':clean(activity.name) if activity else '暂无',
            'minutes':max(0,int((now-activity.started_at).total_seconds()//60)) if activity else None,
            'location':clean(activity.location) if activity else '',
            'remaining_minutes':max(0, int((checkpoint[0]-to_epoch_ms(now))//60000)) if checkpoint else None,
            'counts':counts,'used':used,'budget':runtime.decision_budget,
            'planning':to_local(from_epoch_ms(planning['started_at_ms']),timezone).strftime('%m-%d %H:%M') if planning else '未知',
            'degraded':degraded}


async def hush_reason(ctx, stream_id):
    try:
        response=await asyncio.wait_for(ctx.call_host_method('cap.call',payload={
            'capability':'api.call','args':{'api_name':HUSH_API,'version':'1','args':{'stream_id':stream_id}}}),3)
        result=response.get('result') if isinstance(response,dict) and response.get('success') is True else None
        data=result.get('data') if isinstance(result,dict) and result.get('ok') is True else None
        if not isinstance(data,dict) or data.get('stream_id')!=stream_id or data.get('hard')!='HARD_SILENT': return None
        # Current Hush v1 omits duration. valid_until is freshness (<=5s), NOT
        # the mute deadline. Accept a future explicit duration without guessing.
        remaining=data.get('hard_remaining_seconds')
        if type(remaining) in (int,float) and math.isfinite(remaining) and remaining>=0:
            minutes,seconds=divmod(math.ceil(remaining),60)
            return f'硬静音（MaiHush，剩余 {minutes} 分 {seconds} 秒）'
        return '硬静音（MaiHush，剩余时间未知）'
    except asyncio.CancelledError:
        raise
    except Exception:
        return None


def render_status(data, hush=None):
    activity=data['activity']
    if data['minutes'] is not None: activity+=f'（已进行 {data["minutes"]} 分钟）'
    if data.get('location'): activity+=' | 在：'+data['location']
    if data.get('remaining_minutes') is not None: activity+=f' | 预计还剩 {data["remaining_minutes"]} 分钟'
    counts=data['counts']
    reasons=[]
    if data['phase'] in ('sleeping','temporarily_awake'): reasons.append('睡眠静默（MaiVita）')
    if hush: reasons.append(hush)
    model=f'模型调用：{data["used"]}/{data["budget"]} 次 | 上次规划：{data["planning"]}'
    if data['degraded']: model+=' | 模型状态：degraded（降级）'
    return '\n'.join([
        f'MaiVita：{PHASES.get(data["phase"],"状态未知")}（{data["wake"]}）| 当前活动：{activity}',
        f'今日计划：{sum(counts.values())} 项（{counts["done"]} 完成 / {counts["started"]} 进行中 / {counts["planned"]} 待开始）',
        model,'不回复原因：'+('、'.join(reasons) or '无')])


async def status_text(plugin, stream_id):
    runtime=plugin.runtime
    timezone=plugin.active_config.instance.timezone
    hush=await hush_reason(plugin.ctx,stream_id)
    return render_status(snapshot(runtime,timezone,stream_id),hush)
