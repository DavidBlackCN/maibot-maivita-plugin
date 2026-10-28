"""Optional finite command adapter. All public mutation APIs remain closed."""
import asyncio
from datetime import timedelta
from ..life.domain.commands import Command

FIELD='__maibot_command_ref__'
API='davidblackcn.maibot-maiidentity-plugin.authorize_vita_command'
ACTIONS={'wake':'wake','醒来':'wake','replan':'enqueue_replan','重新规划':'enqueue_replan'}

async def execute(plugin,action,stream_id,ref):
    runtime=plugin.runtime
    if not plugin.ready or not isinstance(ref,str): return plugin.error('HOST_CONTEXT_REQUIRED')
    state=runtime.engine.state()
    try:
        response=await asyncio.wait_for(plugin.ctx.call_host_method('cap.call',payload={
            'capability':'api.call','args':{'api_name':API,'version':'1','args':{FIELD:ref}}}),3)
        decision=response.get('result') if isinstance(response,dict) and response.get('success') is True else None
        if not isinstance(decision,dict) or decision.get('allowed') is not True:
            return plugin.error(decision.get('reason','PERMISSION_DENIED') if isinstance(decision,dict) else 'POLICY_UNAVAILABLE')
        key=decision.get('request_key')
        if (not plugin.ready or plugin.runtime is not runtime or decision.get('action')!=ACTIONS[action]
                or decision.get('stream_id')!=stream_id or not isinstance(key,str) or not key.startswith('host-command:') or len(key)!=77):
            return plugin.error('UNTRUSTED_CONTEXT')
        result=runtime.engine.execute(Command(kind=ACTIONS[action],idempotency_key=key,source='authorized',
            expected_state_version=state.state_version,expected_config_revision=state.config_revision,
            expires_at=runtime.engine.clock.now_utc()+timedelta(seconds=5),payload={'reason':'authorized_command'}))
        return {'ok':True,'data':{'accepted':True,'event_id':result['event_id']},'error':None}
    except asyncio.CancelledError: raise
    except Exception: return plugin.error('MANAGEMENT_UNAVAILABLE_OR_STALE')
