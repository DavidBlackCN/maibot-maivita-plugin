"""Logical API facade for isolated acceptance, not registered host endpoints.

An SDK boundary must supply trusted context, scope and policy functions. Public
arguments never select private_details or command.source.
"""
from ..domain.clock import iso_instant
from ..domain.commands import Command
from ..domain.models import DomainError,require
from ..life_state.projections import QueryScope
from ..compatibility.life_bridge import LegacyLifeBridge


class LifeAPI:
    def __init__(self,runtime,*,scope_resolver=None,authorize=None,wake_authorization=None):
        self.runtime=runtime
        self.scope_resolver=scope_resolver or (lambda context:QueryScope())
        self.authorize=authorize
        self.wake_authorization=wake_authorization

    async def call(self,method,args=None,*,trusted_context=None):
        args=dict(args or {})
        scope=self.scope_resolver(trusted_context)
        require(type(scope) is QueryScope,'untrusted query scope adapter')
        if method=='get_current_activity':
            return LegacyLifeBridge(self.runtime.engine,timezone=self.runtime.config.timezone).get_current_activity(scope)
        try:
            if method in ('get_life_state','get_plan','get_availability','get_diagnostics','get_goals'):
                data=getattr(self.runtime.queries,method)(scope)
            else:
                require(self.authorize is not None and await self.authorize(trusted_context,method,scope) is True,'permission denied')
                if method=='request_replan':
                    require(set(args)=={'idempotency_key','expected_state_version','reason'},'invalid replan arguments')
                    result=self.runtime.engine.execute(Command(kind='enqueue_replan',idempotency_key=args['idempotency_key'],
                        expected_state_version=args['expected_state_version'],source='authorized',payload={'reason':args['reason']}))
                    data={'accepted':True,'request_id':result['event_id']}
                elif method=='propose_commitment':
                    require(set(args)=={'idempotency_key','expected_state_version','proposal'},'invalid commitment arguments')
                    proposal=dict(args['proposal'])
                    proposal['source_stream_id']=scope.stream_id  # trusted source only
                    data=self.runtime.engine.execute(Command(kind='propose_commitment',idempotency_key=args['idempotency_key'],
                        expected_state_version=args['expected_state_version'],source='authorized',payload=proposal))
                elif method=='apply_plan_patch':
                    require(set(args)=={'patch','idempotency_key','expected_state_version'},'invalid plan arguments')
                    data=self.runtime.engine.execute(Command(kind='apply_plan_patch',idempotency_key=args['idempotency_key'],
                        expected_state_version=args['expected_state_version'],source='authorized',payload=args['patch']))
                elif method=='manage_goal':
                    require(set(args)=={'goal','idempotency_key','expected_state_version'},'invalid goal arguments')
                    data=self.runtime.engine.execute(Command(kind='manage_goal',idempotency_key=args['idempotency_key'],
                        expected_state_version=args['expected_state_version'],source='authorized',payload=args['goal']))
                    data={**data,'goal_id':args['goal']['goal_id'],
                        'mapped_action':'cancel_preserving_record' if args['goal']['action']=='delete' else args['goal']['action']}
                elif method=='request_wakeup':
                    require(self.wake_authorization is not None,'policy unavailable')
                    require(set(args)<={'action','idempotency_key','minutes'} and args.get('action') in ('wake','interrupt_sleep'),'invalid wake request')
                    token=await self.wake_authorization.authorize(trusted_context,action=args['action'],stream_id=scope.stream_id)
                    require(token is not None,'permission denied')
                    data=self.wake_authorization.apply(token,stream_id=scope.stream_id,action=args['action'],key=args['idempotency_key'],minutes=args.get('minutes',10))
                else: raise DomainError('unknown method')
            return self._response(True,data,None)
        except (DomainError,ValueError,TypeError,KeyError) as exc:
            message=str(exc)
            code='PERMISSION_DENIED' if 'permission' in message else 'POLICY_UNAVAILABLE' if 'policy unavailable' in message else 'STALE_STATE' if 'stale' in message else 'IDEMPOTENCY_CONFLICT' if 'idempotency' in message else 'CONFLICT_COMMITMENT' if 'commitment' in message else 'INVALID_TIME'
            return self._response(False,None,{'code':code,'message':message,'retryable':False})

    def _response(self,ok,data,error):
        return {'api_version':1,'ok':ok,'data':data,'error':error,
            'state_version':self.runtime.engine.state().state_version,'as_of':iso_instant(self.runtime.engine.clock.now_utc())}
