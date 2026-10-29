"""SDK-only real model boundary. No Host internals, keys, chat reads or retries."""
import asyncio
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


PLANNING_CONTRACT={
    'instruction':('这是每日规划，请生成今天的日程，不要直接开始活动。'
        '只允许 apply_plan_patch 或 update_direction；日程使用角色时区的今天日期。'
        '禁止 start_activity、winding_down、start_sleep、extend_sleep。'
        'Return exactly one JSON object with only kind and payload. '
        'Use apply_plan_patch to propose fixed/window/unscheduled items; free time and empty plans are valid. '
        'Plans are not actual history. Preserve confirmed commitments. '
        'Never create authorization, completion evidence or confirmed commitments. '
        'Private visibility must not be widened. No markdown or reasoning text. '
        '作息偏好只是参考，可以根据实际情况偏离，但偏离时在 payload.reason 字段说明原因。'),
    'commands':{key:PRESENT_CONTRACT['commands'][key] for key in ('apply_plan_patch','update_direction')},
    'example':{'kind':'apply_plan_patch','payload':{'local_date':'2026-10-29',
        'upserts':[{'item_id':'today-study','name':'学习','category':'study','time_mode':'unscheduled',
                    'visibility':'public_summary'}],
        'remove_ids':[],'reason':'根据今天的安排预留学习时间，具体开始时间由即时决策决定'}}
}


def parse_candidate(text):
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
    return candidate(value)


class SDKModelPort:
    def __init__(self,ctx,settings,*,context,routine=None):
        self.ctx,self.settings,self.context=ctx,settings,context
        self.routine=routine or RoutineSettings()
        self.last={'status':'not_called'}

    async def __call__(self,state,triggers,*,task_kind='present'):
        if task_kind not in ('planning','present'): raise DomainError('unknown model task')
        started=time.monotonic()
        context={**self.context(),'routine':self.routine.model_dump()}
        contract=PLANNING_CONTRACT if task_kind=='planning' else PRESENT_CONTRACT
        if task_kind=='planning':
            from ..life.domain.clock import to_local
            today=to_local(context['now'],context.get('character_timezone','Asia/Shanghai')).date().isoformat()
            contract={**contract,'example':{**contract['example'],
                'payload':{**contract['example']['payload'],'local_date':today,
                    'upserts':[{**item,'item_id':item['item_id']+'-'+today} for item in contract['example']['payload']['upserts']]}}}
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
            proposal=validate_task_candidate(parse_candidate(response.get('response')),task_kind)
            self.last={'status':'candidate_validated','model_name':str(response.get('model_name',response.get('model','')))[:128],
                'task_name':self.settings.task if self.settings.mode=='task' else '','elapsed_seconds':round(time.monotonic()-started,3)}
            return proposal
        except asyncio.CancelledError:
            self.last={'status':'cancelled_unknown_outcome'}; raise
        except (TimeoutError,ConnectionError,OSError):
            self.last={'status':'unavailable_unknown_outcome'}; raise
        except Exception:
            self.last={'status':'invalid_response'}; raise
