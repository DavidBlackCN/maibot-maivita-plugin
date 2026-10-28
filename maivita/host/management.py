"""Finite management at the official Command entry. Public writes stay closed."""
import asyncio
import hashlib
import json
import re
from datetime import timedelta
from ..life.domain.commands import Command
from .config import stream_allowed

API='davidblackcn.maibot-maiidentity-plugin.get_vita_policy'
ACTIONS={'wake':'wake','醒来':'wake','replan':'enqueue_replan','重新规划':'enqueue_replan'}


def local_request(plugin, command, bot_account, *, management_action=None):
    # This function is called ONLY by @Command, never by API/Tool dispatch.
    raw=command.get('message')
    if type(raw) is not dict or type(bot_account) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',bot_account):
        raise ValueError('UNTRUSTED_CONTEXT')
    info=raw.get('message_info')
    user=info.get('user_info') if type(info) is dict else None
    group=info.get('group_info') if type(info) is dict else None
    account=user.get('user_id') if type(user) is dict else None
    if type(account) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',account) or account==bot_account:
        raise ValueError('UNTRUSTED_CONTEXT')
    group_id=group.get('group_id') if type(group) is dict else ''
    if group is not None and (type(group) is not dict or type(group_id) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',group_id)):
        raise ValueError('UNTRUSTED_CONTEXT')
    text=command.get('text')
    if type(text) is not str or len(text)>256 or text!=raw.get('processed_plain_text'):
        raise ValueError('UNTRUSTED_CONTEXT')
    words=text.strip().split()
    if management_action is None and (len(words)!=2 or words[0]!='/maivita' or words[1] not in ACTIONS):
        raise ValueError('UNSUPPORTED_COMMAND')
    stream=command.get('stream_id')
    scope=raw.get('scope')
    mid=raw.get('message_id')
    if (raw.get('platform')!='qq' or command.get('platform')!='qq'
            or raw.get('account_id')!=bot_account or command.get('user_id')!=account
            or command.get('group_id')!=group_id or raw.get('session_id')!=stream
            or not stream_allowed(stream,plugin.active_config.instance.allowed_streams)
            or type(mid) is not str or not 0<len(mid)<=256
            or (scope is not None and (type(scope) is not str or not 0<len(scope)<=256))
            or any(raw.get(flag,False) is not False for flag in ('is_bot','is_system','is_mai_message','is_notify'))):
        raise ValueError('UNTRUSTED_CONTEXT')
    query={'account_id':account,'bot_account_id':bot_account,'route_scope':scope,
        'stream_id':stream,'action':management_action if management_action is not None else ACTIONS[words[1]]}
    # Same original message cannot be replayed as another action. The engine
    # checks payload conflicts and persists idempotency across reloads.
    key='local-command:'+hashlib.sha256(json.dumps(
        [bot_account,scope,account,stream,mid],ensure_ascii=False).encode()).hexdigest()
    return query,key


async def execute(plugin,command):
    runtime=plugin.runtime
    cfg=plugin.active_config
    if not plugin.ready or runtime is None or cfg is None: return plugin.error('NOT_READY')
    state=runtime.engine.state()
    try:
        bot=await asyncio.wait_for(plugin.ctx.config.get('bot.qq_account',''),2)
        if not plugin.ready or plugin.runtime is not runtime or plugin.active_config is not cfg:
            return plugin.error('NOT_READY')
        query,key=local_request(plugin,command,str(bot))
        response=await asyncio.wait_for(plugin.ctx.call_host_method('cap.call',payload={
            'capability':'api.call','args':{'api_name':API,'version':'1','args':query}}),3)
        decision=response.get('result') if type(response) is dict and response.get('success') is True else None
        if type(decision) is not dict or decision.get('policy_version')!=1 or decision.get('allowed') is not True:
            return plugin.error('PERMISSION_DENIED')
        if not plugin.ready or plugin.runtime is not runtime or plugin.active_config is not cfg:
            return plugin.error('NOT_READY')
        result=runtime.engine.execute(Command(kind=query['action'],idempotency_key=key,source='authorized',
            expected_state_version=state.state_version,expected_config_revision=state.config_revision,
            expires_at=runtime.engine.clock.now_utc()+timedelta(seconds=5),payload={'reason':'authorized_command'}))
        return {'ok':True,'data':{'accepted':True,'event_id':result['event_id']},'error':None}
    except asyncio.CancelledError: raise
    except Exception: return plugin.error('MANAGEMENT_UNAVAILABLE_OR_STALE')
