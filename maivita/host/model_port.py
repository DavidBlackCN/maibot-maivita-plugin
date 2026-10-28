"""SDK-only real model boundary. No Host internals, keys, chat reads or retries."""
import asyncio
import json
import time

from ..life.domain.clock import iso_instant
from ..life.domain.models import DomainError
from ..life.planning.hybrid import candidate

CONTRACT={
    'instruction':('Return exactly one JSON object with only kind and payload. '
        'Choose a legal present intent or incremental plan. Plans are not actual history. '
        'Never create authorization, completion evidence or confirmed commitments. '
        'Private visibility must not be widened. No markdown or reasoning text.'),
    'commands':{
        'start_activity':{'name':'string','category':'rest|study|work|entertainment|daily_life|social|travel|creative|other',
            'description':'string','visibility':'public_summary','check_after_minutes':'number >0 and <=1440'},
        'winding_down':{'reason':'string'},
        'start_sleep':{'duration_minutes':'5..1080','kind':'night_sleep|nap','reason':'nonempty string'},
        'apply_plan_patch':{'local_date':'YYYY-MM-DD','upserts':'ScheduleItem[] with item_id/name/time_mode',
            'remove_ids':'string[]','reason':'nonempty string'},
        'update_direction':{'goals':'objects with goal_id/title/stage/status','reason':'nonempty string'},
        'extend_sleep':{'expected_wake_at':'offset-aware ISO instant','reason':'nonempty string'}},
    'example':{'kind':'start_activity','payload':{'name':'安静休息','category':'rest','visibility':'public_summary','check_after_minutes':30}}
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
    def __init__(self,ctx,settings,*,context):
        self.ctx,self.settings,self.context=ctx,settings,context
        self.last={'status':'not_called'}

    async def __call__(self,state,triggers):
        started=time.monotonic()
        context=self.context()
        from .persona import read_persona
        persona=await read_persona(self.ctx,self.settings.life_context)
        prompt=json.dumps({'contract':CONTRACT,'as_of':iso_instant(context['now']),
            'basis':{'state_version':state.state_version,'plan_version':state.plan_version,'config_revision':state.config_revision},
            'context':{k:v for k,v in context.items() if k!='now'},
            'character_context':persona['text'],'trigger_ids':list(triggers)[:100]},ensure_ascii=False)
        if len(prompt)>40000: raise DomainError('model context exceeds I1 limit')
        try:
            response=await asyncio.wait_for(self.ctx.llm.generate(prompt,task_name=self.settings.task_name,
                model_name=self.settings.model_name,max_tokens=self.settings.max_tokens,
                rpc_timeout_ms=self.settings.timeout_seconds*1000),self.settings.timeout_seconds)
            if not isinstance(response,dict) or response.get('success') is not True:
                raise ConnectionError('Host model service did not return success')
            proposal=parse_candidate(response.get('response'))
            self.last={'status':'candidate_validated','model_name':str(response.get('model_name',response.get('model','')))[:128],
                'task_name':self.settings.task_name,'elapsed_seconds':round(time.monotonic()-started,3)}
            return proposal
        except asyncio.CancelledError:
            self.last={'status':'cancelled_unknown_outcome'}; raise
        except (TimeoutError,ConnectionError):
            self.last={'status':'unavailable_unknown_outcome'}; raise
        except Exception:
            self.last={'status':'invalid_response'}; raise
