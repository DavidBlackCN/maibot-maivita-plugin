"""SDK-only real model boundary. No Host internals, keys, chat reads or retries."""
import asyncio
from datetime import datetime, timedelta
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
        '这是每日参考规划。完整一天通常安排 10-16 项有意义的生活阶段，数量只是参考，不设硬下限。'
        '只允许 apply_plan_patch；日程使用角色时区的今天日期。'
        '禁止 start_activity、winding_down、start_sleep、extend_sleep——这是规划，不是执行，不要直接开始活动。'
        '\n\n'
        '【时间模式】每个活动必须选择一种 time_mode：'
        '\n- fixed：明确的开始和结束时间，用 start_at/end_at（ISO 8601，带时区）。'
        '  适合有确定时间的活动（上课、会议、固定饭点、睡觉）。'
        '\n- window：一个时间窗内灵活安排，用 window_start_at/window_end_at + '
        'preferred_duration_minutes。适合「下午学习 1-2 小时」这类不精确的事。'
        '\n- unscheduled：今天想做但不定时，只有 name。'
        '  适合不需要定时的打算，不要为了凑数量添加事项。'
        '\n\n'
        '【时间与粒度】'
        '\n- 睡眠、吃饭、洗漱计入总数；不在这些基础活动外额外安排十几项正事。'
        '\n- 根据当前当地时间安排尚未过去的时间；晚启动或重生成只安排剩余时间，不补编过去。'
        '\n- 活动按目的、场景或专注程度的变化划分；普通活动多为半小时到两小时，睡眠和长活动可更久。'
        '\n- 喝水、翻身等微动作合并到休息或日常活动；晨间洗漱护肤通常合并。'
        '\n- 合理的睡眠可以跨午夜；三餐可按人设合并为早午餐，不强迫固定套路。'
        '\n- 安排有大致时间的生活，也允许空档；不要求无缝覆盖 24 小时。'
        '\n- 活动之间不能时间重叠（fixed 项）。'
        '\n\n'
        '【生活质感】'
        '\n- 不要排成流程模板（起床→上班→午休→下班→晚饭→睡觉一条龙）。'
        '\n- 允许稳定重复，也允许临时起意、无所事事、赖床、发呆，不强求每天不同。'
        '\n- 结合人设、近期已记录的生活、作息偏好（只是参考）、当地日期和星期；没有外部依据时不编造天气或节日。'
        '\n- 计划写打算，不提前写死买到了什么、学会了什么、情绪反应或他人的行为。'
        '\n- 有明确时间的事要合理（不要凌晨 3 点学习，除非人设是夜猫子）。'
        '\n\n'
        '【输出】'
        '\nexample 只是随当前时间裁剪的格式示范，不要求同样条数，也不要照抄活动或 item_id。'
        '每项使用有意义且不与其他日期混淆的 item_id；修改已有事项时保留原 item_id。'
        '\nReturn exactly one JSON object with only kind and payload. '
        'Plans are not actual history. Preserve confirmed commitments. '
        'Never create authorization, completion evidence or confirmed commitments. '
        'Private visibility must not be widened. No markdown or reasoning text. '
        '作息偏好只是参考，可以根据实际情况偏离，但偏离时在 payload.reason 字段说明原因。'
    ),
    'commands': {key: PRESENT_CONTRACT['commands'][key] for key in ('apply_plan_patch',)},
    'example': {
        'kind': 'apply_plan_patch',
        'payload': {
            'local_date': '2026-10-29',
            'upserts': [
                {'item_id': 'night', 'name': '睡眠', 'category': 'rest',
                 'time_mode': 'fixed', 'start_at': '2026-10-29T01:00:00+08:00',
                 'end_at': '2026-10-29T08:00:00+08:00', 'visibility': 'public_summary'},
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
                {'item_id': 'd5', 'name': '午后休息，随便看看', 'category': 'rest',
                 'time_mode': 'window',
                 'window_start_at': '2026-10-29T13:00:00+08:00',
                 'window_end_at': '2026-10-29T14:30:00+08:00',
                 'preferred_duration_minutes': 90,
                 'visibility': 'public_summary'},
                {'item_id': 'afternoon', 'name': '阅读感兴趣的书', 'category': 'entertainment',
                 'time_mode': 'window', 'window_start_at': '2026-10-29T14:30:00+08:00',
                 'window_end_at': '2026-10-29T16:00:00+08:00',
                 'preferred_duration_minutes': 90, 'visibility': 'public_summary'},
                {'item_id': 'walk', 'name': '去附近散步，顺路逛逛', 'category': 'travel',
                 'time_mode': 'window', 'window_start_at': '2026-10-29T16:00:00+08:00',
                 'window_end_at': '2026-10-29T17:30:00+08:00',
                 'preferred_duration_minutes': 60, 'visibility': 'public_summary'},
                {'item_id': 'd6', 'name': '晚饭', 'category': 'daily_life',
                 'time_mode': 'fixed',
                 'start_at': '2026-10-29T18:00:00+08:00',
                 'end_at': '2026-10-29T19:00:00+08:00',
                 'visibility': 'public_summary'},
                {'item_id': 'game', 'name': '玩一会儿游戏', 'category': 'entertainment',
                 'time_mode': 'window', 'window_start_at': '2026-10-29T19:00:00+08:00',
                 'window_end_at': '2026-10-29T20:30:00+08:00',
                 'preferred_duration_minutes': 90, 'visibility': 'public_summary'},
                {'item_id': 'wash', 'name': '洗漱整理', 'category': 'daily_life',
                 'time_mode': 'window', 'window_start_at': '2026-10-29T20:30:00+08:00',
                 'window_end_at': '2026-10-29T21:00:00+08:00',
                 'preferred_duration_minutes': 30, 'visibility': 'public_summary'},
                {'item_id': 'relax', 'name': '听歌阅读，放松一下', 'category': 'rest',
                 'time_mode': 'window', 'window_start_at': '2026-10-29T21:00:00+08:00',
                 'window_end_at': '2026-10-29T23:00:00+08:00',
                 'preferred_duration_minutes': 90, 'visibility': 'public_summary'},
                {'item_id': 'sleep', 'name': '夜间睡眠', 'category': 'rest',
                 'time_mode': 'fixed', 'start_at': '2026-10-29T23:00:00+08:00',
                 'end_at': '2026-10-30T08:00:00+08:00', 'visibility': 'public_summary'},
            ],
            'remove_ids': [],
            'reason': '这是格式示例：包含日常起居，保留空档，睡眠可以跨午夜；请按人设和当下时间安排'
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
        '\n- 最近24小时已经记录的生活（包括午夜前），不是计划中的未来经历'
        '\n- 今天的大致样子（若有）'
        '\n\n'
        '【怎么决定】'
        '\n1. 身体读数是输入：累到不行就休息/睡觉；吃饭参考当地时间、人设和近期活动，不假设存在饥饿读数'
        '\n2. 一件事做到一半接着做很正常（continue=true）；做很久了、或身体不允许时就换'
        '\n3. minutes 描述一个生活阶段：阅读、休息或出门通常半小时到两小时；喝水等微动作并入当前活动，遵守 context 中时长边界'
        '\n4. 换一件事要接得上：上一段在哪、刚做完什么，换地方要有说得过去的由头'
        '\n5. 可以临时起意，但没有依据就不编造朋友来访、快递通知或真实平台互动；观察缺口不能补编成经历'
        '\n6. 别把一天排成流程模板；午睡不是每天必须有'
        '\n7. 允许无所事事、发呆、刷手机——这也是她的一天'
        '\n\n'
        '【输出】只输出一个 JSON 对象：'
        '\n{"event": "在做什么", "location": "在哪", "emotion": "心情", '
        '"minutes": 45, "continue": false, "category": "rest", "sleep": false}'
        '\n- event 要具体到能说出口，不要把翻身等微动作单列。sleep=true 表示真正睡眠，名称可自然描述；清醒休息用 sleep=false。'
        '\n- continue=true 不改变当前睡眠状态，sleep 可省略；睡眠只能依既有唤醒规则结束。'
        '\n- continue=true 时 event/location/emotion 可留空'
        '\n- category：rest|study|work|entertainment|daily_life|social|travel|creative|other。身体变化由固定类别规则计算，不输出变化速率。'
    ),
    'example': {'event': '在家读小说', 'location': '家里的沙发', 'emotion': '放松', 'minutes': 45, 'continue': False, 'category': 'rest', 'sleep': False}
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
            reference_date=datetime.fromisoformat(contract['example']['payload']['local_date']).date()
            for template in contract['example']['payload']['upserts']:
                item={**template,'item_id':template['item_id']+'-'+today}
                for key in ('start_at','end_at','window_start_at','window_end_at'):
                    if key in item:
                        original=datetime.fromisoformat(item[key])
                        day=local_now.date()+(original.date()-reference_date)
                        item[key]=original.replace(year=day.year,month=day.month,day=day.day,
                            tzinfo=local_now.tzinfo).isoformat()
                start_key='start_at' if item['time_mode']=='fixed' else 'window_start_at'
                end_key='end_at' if item['time_mode']=='fixed' else 'window_end_at'
                if end_key in item:
                    end=datetime.fromisoformat(item[end_key])
                    if end<=local_now:
                        continue
                    if datetime.fromisoformat(item[start_key])<local_now:
                        item[start_key]=local_now.isoformat()
                    if 'preferred_duration_minutes' in item:
                        available=int((end-datetime.fromisoformat(item[start_key])).total_seconds()/60)
                        if available<1: continue
                        item['preferred_duration_minutes']=min(item['preferred_duration_minutes'],available)
                items.append(item)
            if not items:
                items=[{'item_id':'evening-'+today,'name':'睡前放松','category':'rest','time_mode':'window',
                    'window_start_at':local_now.isoformat(),'window_end_at':(local_now+timedelta(minutes=30)).isoformat(),
                    'preferred_duration_minutes':30,'visibility':'public_summary'}]
            context['planning_window']={'not_before':local_now.isoformat(),
                'local_date':today,'weekday':('星期一','星期二','星期三','星期四','星期五','星期六','星期日')[local_now.weekday()],
                'note':'只安排从现在起的参考生活；跨午夜睡眠允许延续。过去只能引用已记录的活动。'}
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
