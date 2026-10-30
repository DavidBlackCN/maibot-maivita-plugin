"""Detailed status for the explicit status command only; never a prompt/API feed."""
import asyncio
import json
import math
from collections import Counter

from ..life.domain.clock import from_epoch_ms, local_day_bounds, to_epoch_ms, to_local
from ..life.life_state.projections import QueryScope, visible
from ..life.life_state.subjective import body_value_warning
from ..life.planning.hybrid import decision_task_kind
from ..life.planning.availability import LABELS, derive_availability
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
        availability=derive_availability(state,activity,runtime.config.availability,
            activity_visible=activity is not None and visible(activity,scope))
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
            'body':{key:getattr(state,key) for key in ('energy','fatigue','stress')},
            'next_check_seconds':max(0,int(runtime.config.scheduler.decision_interval_minutes*60-
                (runtime.engine.clock.monotonic()-runtime.last_segment_check))) if runtime.last_segment_check is not None else 0,
            'body_warnings':[(field,getattr(state,field)) for field in ('energy','fatigue','stress')
                             if body_value_warning(field,getattr(state,field))],
            'availability':availability.state,'availability_reason':availability.reason,
            'availability_enabled':runtime.config.availability.enabled,
            'wake':to_local(state.last_wake_at,timezone).strftime('%m-%d %H:%M') if state.last_wake_at else '未知',
            'activity':clean(activity.name) if activity else '暂无',
            'minutes':max(0,int((now-activity.started_at).total_seconds()//60)) if activity else None,
            'location':clean(activity.location) if activity else '',
            'remaining_minutes':max(0, int((checkpoint[0]-to_epoch_ms(now))//60000)) if checkpoint else None,
            'counts':counts,'used':used,'budget':runtime.decision_budget,
            'check_minutes':runtime.config.scheduler.decision_interval_minutes,
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
    elif data.get('availability_enabled') and data.get('availability')=='busy':
        reasons.append('忙碌静默（MaiVita：'+data['availability_reason']+'）')
    if hush: reasons.append(hush)
    model=f'模型调用：{data["used"]}/{data["budget"]} 次 | 上次规划：{data["planning"]}'
    if data['degraded']: model+=' | 模型状态：degraded（降级）'
    lines=[
        f'MaiVita：{PHASES.get(data["phase"],"状态未知")}（{data["wake"]}）| 当前活动：{activity}',
        f'今日参考计划：{sum(counts.values())} 项（不作为实际经历或完成证明）',
        '身体依据：固定活动类别规则（模拟估计）；睡眠以真实睡眠会话识别',
        f'生活检查间隔：{data.get("check_minutes",15)} 分钟（检查不等于调用模型）',
        model,'不回复原因：'+('、'.join(reasons) or '无')]
    if 'availability' in data:
        state=data['availability']
        behavior='忙闲守卫已开启' if data['availability_enabled'] else '忙闲守卫已关闭'
        if state=='slow': behavior+='；专注时仍可正常回复，不延迟或补发'
        lines.insert(1,f'状态：{state}（{LABELS[state]}）| 原因：{data["availability_reason"]} | {behavior}')
    if data.get('body_warnings'):
        labels={'energy':'精力','fatigue':'疲劳','stress':'压力'}
        values='、'.join(f'{labels[field]} {value:.2f}' for field,value in data['body_warnings'])
        lines.append('⚠ 身体数值异常：'+values+'（请检查活动历史或测试数据）')
    if data.get('body'):
        labels={'energy':'精力','fatigue':'疲劳','stress':'压力'}
        lines.append('身体估计：'+' / '.join(labels[k]+' '+('未知' if v is None else f'{v:.1f}') for k,v in data['body'].items())+
            f'；下次检查约 {data.get("next_check_seconds",0)} 秒后')
    return '\n'.join(lines)


async def status_text(plugin, stream_id):
    runtime=plugin.runtime
    timezone=plugin.active_config.instance.timezone
    hush=await hush_reason(plugin.ctx,stream_id)
    return render_status(snapshot(runtime,timezone,stream_id),hush)
