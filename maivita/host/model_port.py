"""SDK-only real model boundary. No Host internals, keys, chat reads or retries."""
import asyncio
from datetime import datetime
import json
import time

from ..life.domain.clock import iso_instant
from ..life.domain.models import DomainError
from ..life.planning.hybrid import candidate, validate_task_candidate
from .config import RoutineSettings

PRESENT_CONTRACT={
    'instruction':('Return exactly one JSON object with only kind and payload. '
        'Choose a legal present intent or incremental plan. Plans are not actual history. '
        'Never create authorization, completion evidence or confirmed commitments. '
        'Private visibility must not be widened. No markdown or reasoning text. '
        '作息偏好只是参考，可以根据实际情况偏离，但偏离时在 payload.reason 字段说明原因。'),
    'commands':{
        'start_activity':{'name':'string','category':'rest|study|work|entertainment|daily_life|social|travel|creative|other',
            'description':'string','reason':'string explaining deviation when needed','visibility':'public_summary','check_after_minutes':'number >0 and <=1440'},
        'winding_down':{'reason':'string'},
        'start_sleep':{'duration_minutes':'5..1080','kind':'night_sleep|nap','reason':'nonempty string'},
        'apply_plan_patch':{'local_date':'YYYY-MM-DD','upserts':'ScheduleItem[] with item_id/name/time_mode',
            'remove_ids':'string[]','reason':'nonempty string'},
        'update_direction':{'goals':'objects with goal_id/title/stage/status','reason':'nonempty string'},
        'extend_sleep':{'expected_wake_at':'offset-aware ISO instant','reason':'nonempty string'}},
    'example':{'kind':'start_activity','payload':{'name':'安静休息','category':'rest','visibility':'public_summary','check_after_minutes':30}}
}


PLANNING_CONTRACT = {
    'instruction': (
        '这是每日规划。请为角色生成今天的一整天日程，共 6-12 个活动。'
        '只允许 apply_plan_patch 或 update_direction；日程使用角色时区的今天日期。'
        '禁止 start_activity、winding_down、start_sleep、extend_sleep——这是规划，不是执行，不要直接开始活动。'
        '\n\n'
        '【时间模式】每个活动必须选择一种 time_mode：'
        '\n- fixed：明确的开始和结束时间，用 start_at/end_at（ISO 8601，带时区）。'
        '  适合有确定时间的活动（上课、会议、固定饭点、睡觉）。'
        '\n- window：一个时间窗内灵活安排，用 window_start_at/window_end_at + '
        'preferred_duration_minutes。适合「下午学习 1-2 小时」这类不精确的事。'
        '\n- unscheduled：今天想做但不定时，只有 name。'
        '  只用于确实无法定时的事，且全天最多 2 条。'
        '\n\n'
        '【覆盖要求】'
        '\n- 6-12 个活动，覆盖早/中/晚主要时段（不能只写上午或只写晚上）。'
        '\n- 至少 3 条必须是 fixed 或 window（有明确时间），不能全是 unscheduled。'
        '\n- 允许且鼓励用 free_time / rest / daily_life 表达空白时段，不要排满 24 小时。'
        '\n- 活动之间不能时间重叠（fixed 项）。'
        '\n\n'
        '【生活质感】'
        '\n- 不要排成流程模板（起床→上班→午休→下班→晚饭→睡觉一条龙）。'
        '\n- 每天不应该长得差不多；允许临时起意、无所事事、赖床、发呆。'
        '\n- 结合人设、作息偏好（只是参考）、当前季节/节日。'
        '\n- 有明确时间的事要合理（不要凌晨 3 点学习，除非人设是夜猫子）。'
        '\n\n'
        '【输出】'
        '\nexample 只是格式示范，不要求恰好 6 条，也不要照抄活动或 item_id。'
        '每项使用有意义且不与其他日期混淆的 item_id；修改已有事项时保留原 item_id。'
        '\nReturn exactly one JSON object with only kind and payload. '
        'Plans are not actual history. Preserve confirmed commitments. '
        'Never create authorization, completion evidence or confirmed commitments. '
        'Private visibility must not be widened. No markdown or reasoning text. '
        '作息偏好只是参考，可以根据实际情况偏离，但偏离时在 payload.reason 字段说明原因。'
    ),
    'commands': {key: PRESENT_CONTRACT['commands'][key] for key in ('apply_plan_patch', 'update_direction')},
    'example': {
        'kind': 'apply_plan_patch',
        'payload': {
            'local_date': '2026-10-29',
            'upserts': [
                {'item_id': 'd1', 'name': '起床洗漱', 'category': 'daily_life',
                 'time_mode': 'fixed',
                 'start_at': '2026-10-29T08:00:00+08:00',
                 'end_at': '2026-10-29T08:40:00+08:00',
                 'visibility': 'public_summary'},
                {'item_id': 'd2', 'name': '早餐', 'category': 'daily_life',
                 'time_mode': 'fixed',
                 'start_at': '2026-10-29T08:40:00+08:00',
                 'end_at': '2026-10-29T09:10:00+08:00',
                 'visibility': 'public_summary'},
                {'item_id': 'd3', 'name': '学习', 'category': 'study',
                 'time_mode': 'window',
                 'window_start_at': '2026-10-29T09:30:00+08:00',
                 'window_end_at': '2026-10-29T12:00:00+08:00',
                 'preferred_duration_minutes': 120,
                 'visibility': 'public_summary'},
                {'item_id': 'd4', 'name': '午饭', 'category': 'daily_life',
                 'time_mode': 'fixed',
                 'start_at': '2026-10-29T12:00:00+08:00',
                 'end_at': '2026-10-29T13:00:00+08:00',
                 'visibility': 'public_summary'},
                {'item_id': 'd5', 'name': '自由时间', 'category': 'free_time',
                 'time_mode': 'window',
                 'window_start_at': '2026-10-29T13:00:00+08:00',
                 'window_end_at': '2026-10-29T18:00:00+08:00',
                 'preferred_duration_minutes': 180,
                 'visibility': 'public_summary'},
                {'item_id': 'd6', 'name': '晚饭', 'category': 'daily_life',
                 'time_mode': 'fixed',
                 'start_at': '2026-10-29T18:00:00+08:00',
                 'end_at': '2026-10-29T19:00:00+08:00',
                 'visibility': 'public_summary'},
            ],
            'remove_ids': [],
            'reason': '这是格式示例：6 个活动覆盖早中晚，fixed 与 window 混用，空档用 free_time 表达'
        }
    }
}


SEGMENT_CONTRACT = {
    'instruction': (
        '你正在决定角色【从现在开始的这一小段】在做什么。'
        '不是排一整天，只决定这一小段，之后的事到了时候会再决定。'
        '\n\n'
        '【决策依据】'
        '\n- 她是谁（人设）：优先级最高'
        '\n- 上一段在做什么、做了多久、还剩多久'
        '\n- 她现在的身体读数（精力、疲劳、压力、睡眠债）'
        '\n- 今天已经过完的时段'
        '\n- 今天的大致样子（若有）'
        '\n\n'
        '【怎么决定】'
        '\n1. 身体读数是输入：累到不行就休息/睡觉，饿到不行就去吃——怎么权衡由你判断'
        '\n2. 一件事做到一半接着做很正常（continue=true）；做很久了、或身体不允许时就换'
        '\n3. minutes 要真实：喝口水 15-30 分钟，出门办事 1-3 小时；别因为要填时间就往长了写'
        '\n4. 换一件事要接得上：上一段在哪、刚做完什么，换地方要有说得过去的由头'
        '\n5. 一天可以被小事打断（快递到了、朋友叫住）；也可以临时起意（突然想出去走走）'
        '\n6. 别把一天排成流程模板；午睡不是每天必须有'
        '\n7. 允许无所事事、发呆、刷手机——这也是她的一天'
        '\n\n'
        '【输出】只输出一个 JSON 对象：'
        '\n{"event": "在做什么", "location": "在哪", "emotion": "心情", '
        '"minutes": 45, "continue": false, "energy_rate": -0.08}'
        '\n- event 要具体到能说出口（不要"翻个身"这种微动作；睡眠直接写"睡眠"）'
        '\n- continue=true 时 event/location/emotion 可留空'
        '\n- energy_rate：睡眠/休息为正（0.05~0.2），工作/外出/社交为负（-0.05~-0.15）'
    ),
    'example': {'event': '在家读小说', 'location': '家里的沙发', 'emotion': '放松', 'minutes': 45, 'continue': False, 'energy_rate': -0.05}
}

def parse_candidate(text, *, task_kind='present'):
    if not isinstance(text,str) or len(text)>65536: raise DomainError('invalid model response size')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise DomainError('duplicate model JSON key')
            result[key]=value
        return result
    def reject_constant(value): raise DomainError('non-finite model number')
    try: value=json.loads(text,object_pairs_hook=unique,parse_constant=reject_constant)
    except (ValueError,RecursionError) as error: raise DomainError('invalid model JSON') from error
    if task_kind == 'segment':
        from ..life.domain.segment import validate_segment
        return {'kind': 'advance_segment', 'payload': validate_segment(value)}
    return candidate(value)


class SDKModelPort:
    def __init__(self,ctx,settings,*,context,routine=None):
        self.ctx,self.settings,self.context=ctx,settings,context
        self.routine=routine or RoutineSettings()
        self.last={'status':'not_called'}

    async def __call__(self,state,triggers,*,task_kind='present'):
        if task_kind not in ('planning','present','segment'): raise DomainError('unknown model task')
        started=time.monotonic()
        # Weekly patterns are configuration-only until the next planning stage.
        context={**self.context(),'routine':self.routine.model_dump(exclude={'weekly_pattern','initialize_body_state'})}
        contract=SEGMENT_CONTRACT if task_kind=='segment' else PLANNING_CONTRACT if task_kind=='planning' else PRESENT_CONTRACT
        if task_kind=='planning':
            from ..life.domain.clock import to_local
            local_now=to_local(context['now'],context.get('character_timezone','Asia/Shanghai'))
            today=local_now.date().isoformat()
            items=[]
            for template in contract['example']['payload']['upserts']:
                item={**template,'item_id':template['item_id']+'-'+today}
                for key in ('start_at','end_at','window_start_at','window_end_at'):
                    if key in item:
                        item[key]=datetime.fromisoformat(item[key]).replace(
                            year=local_now.year,month=local_now.month,day=local_now.day,
                            tzinfo=local_now.tzinfo).isoformat()
                items.append(item)
            contract={**contract,'example':{**contract['example'],
                'payload':{**contract['example']['payload'],'local_date':today,'upserts':items}}}
        from .persona import read_persona
        persona=await read_persona(self.ctx,self.settings.life_context)
        prompt=json.dumps({'task_kind':task_kind,'contract':contract,'as_of':iso_instant(context['now']),
            'basis':{'state_version':state.state_version,'plan_version':state.plan_version,'config_revision':state.config_revision},
            'context':{k:v for k,v in context.items() if k!='now'},
            'character_context':persona['text'],'trigger_ids':list(triggers)[:100]},ensure_ascii=False)
        if len(prompt)>40000: raise DomainError('model context exceeds I1 limit')
        route=({'task_name':self.settings.task} if self.settings.mode=='task'
               else {'model_name':self.settings.model})
        try:
            response=await asyncio.wait_for(self.ctx.llm.generate(prompt,**route,
                max_tokens=self.settings.max_tokens,
                rpc_timeout_ms=self.settings.timeout_seconds*1000),self.settings.timeout_seconds)
            if not isinstance(response,dict) or response.get('success') is not True:
                raise ConnectionError('Host model service did not return success')
            proposal=validate_task_candidate(parse_candidate(response.get('response'), task_kind=task_kind),task_kind)
            self.last={'status':'candidate_validated','model_name':str(response.get('model_name',response.get('model','')))[:128],
                'task_name':self.settings.task if self.settings.mode=='task' else '','elapsed_seconds':round(time.monotonic()-started,3)}
            return proposal
        except asyncio.CancelledError:
            self.last={'status':'cancelled_unknown_outcome'}; raise
        except (TimeoutError,ConnectionError,OSError):
            self.last={'status':'unavailable_unknown_outcome'}; raise
        except Exception:
            self.last={'status':'invalid_response'}; raise
