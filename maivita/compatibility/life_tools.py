"""Legacy names mapped to the scoped logical API in isolated runtime.

Deletion maps explicitly to cancellation while retaining the audit record;
completion requires actual evidence. There is no old GoalManager fallback.
The R0 plugin continues its original service separately.
"""
from .life_bridge import LegacyLifeBridge


class LifeToolAdapter:
    def __init__(self,api):
        self.api=api

    async def call(self,name,args,*,trusted_context=None):
        runtime=self.api.runtime
        if name in ('get_planning_status_v4','status'):
            return await self.api.call('get_plan',trusted_context=trusted_context)
        if name in ('generate_schedule_v4','regenerate'):
            return await self.api.call('request_replan',{'reason':args.get('reason','legacy explicit regeneration'),
                'idempotency_key':args['idempotency_key'],'expected_state_version':args['expected_state_version']},trusted_context=trusted_context)
        if name=='apply_schedule_v4':
            payload=LegacyLifeBridge(runtime.engine,timezone=runtime.config.timezone).convert_schedule(
                args['schedule_data'],local_date=args['local_date'],key=args['idempotency_key'])
            return await self.api.call('apply_plan_patch',{'patch':payload,'idempotency_key':args['idempotency_key'],
                'expected_state_version':args['expected_state_version']},trusted_context=trusted_context)
        if name=='update_schedule_v4':
            return await self.api.call('propose_commitment',{'proposal':{'title':args['description'],
                'local_date':args['local_date'],'notes':'legacy candidate; not confirmed'},
                'idempotency_key':args['idempotency_key'],'expected_state_version':args['expected_state_version']},trusted_context=trusted_context)
        if name=='manage_goal_v4':
            from ..life.life_state.engine import digest
            action=args['action']
            if action in ('list','get'):
                result=await self.api.call('get_goals',trusted_context=trusted_context)
                if action=='get' and result['ok']: result['data']=[g for g in result['data'] if g['goal_id']==args['goal_id']]
                return result
            goal={'action':action,'goal_id':args.get('goal_id') or 'tool-goal-'+digest({'key':args['idempotency_key']})}
            for old,new in [('name','title'),('description','stage'),('deadline_at','deadline_at'),('progress_evidence','progress_evidence')]:
                if old in args: goal[new]=args[old]
            return await self.api.call('manage_goal',{'goal':goal,'idempotency_key':args['idempotency_key'],
                'expected_state_version':args['expected_state_version']},trusted_context=trusted_context)
        return {'api_version':1,'ok':False,'data':None,'error':{'code':'NOT_READY',
            'message':'legacy operation requires an explicit domain mapping; old database fallback is disabled','retryable':False}}
