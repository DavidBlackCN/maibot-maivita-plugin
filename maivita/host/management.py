"""Finite management at the official Command entry. Public writes stay closed."""
import asyncio
import hashlib
import json
import logging
import re
from datetime import timedelta
from ..life.domain.commands import Command
from .config import stream_allowed
from .routes import command_route

API='davidblackcn.maibot-maiidentity-plugin.get_vita_policy'
logger=logging.getLogger(__name__)


def diagnostic(reason_code, stage, present, type_ok, matches):
    fields={'reason_code':reason_code,'stage':stage,'present':bool(present),
        'type_ok':bool(type_ok),'matches':bool(matches)}
    logger.warning(json.dumps(fields,sort_keys=True),extra=fields)


def reject(reason_code, stage, present, type_ok, matches):
    diagnostic(reason_code,stage,present,type_ok,matches)
    raise ValueError('UNTRUSTED_CONTEXT')


ACTIONS={'wake':'wake','醒来':'wake','replan':'enqueue_replan','重新规划':'enqueue_replan'}


def local_request(plugin, command, bot_account, *, management_action=None):
    # This function is called ONLY by @Command, never by API/Tool dispatch.
    raw=command.get('message')
    if type(raw) is not dict:
        reject('CONTEXT_MESSAGE_NOT_DICT','message', 'message' in command,type(raw) is dict,False)
    if type(bot_account) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',bot_account):
        reject('CONTEXT_BOT_ACCOUNT_INVALID','bot_account',bot_account is not None,type(bot_account) is str,False)
    info=raw.get('message_info')
    user=info.get('user_info') if type(info) is dict else None
    group=info.get('group_info') if type(info) is dict else None
    account=user.get('user_id') if type(user) is dict else None
    if type(account) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',account) or account==bot_account:
        if type(info) is not dict or type(user) is not dict:
            reject('CONTEXT_USER_INFO_MISSING','user_info',type(info) is dict and 'user_info' in info,type(user) is dict,False)
        reject('CONTEXT_USER_ACCOUNT_INVALID','user_account','user_id' in user,type(account) is str,False)
    group_id=group.get('group_id') if type(group) is dict else ''
    if group is not None and (type(group) is not dict or type(group_id) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',group_id)):
        reject('CONTEXT_GROUP_INFO_INVALID','group_info',True,
            type(group) is dict and type(group_id) is str,False)
    text=command.get('text')
    if type(text) is not str or len(text)>256 or text!=raw.get('processed_plain_text'):
        reject('CONTEXT_TEXT_MISMATCH','text','text' in command,type(text) is str,
            type(text) is str and text==raw.get('processed_plain_text'))
    words=text.strip().split()
    if management_action is None and (len(words)!=2 or words[0]!='/maivita' or words[1] not in ACTIONS):
        raise ValueError('UNSUPPORTED_COMMAND')
    stream=command.get('stream_id')
    scope=raw.get('scope')
    mid=raw.get('message_id')
    if raw.get('platform')!='qq':
        reject('CONTEXT_PLATFORM_MISMATCH','raw_platform','platform' in raw,type(raw.get('platform')) is str,False)
    if command.get('platform')!='qq':
        reject('CONTEXT_PLATFORM_MISMATCH','command_platform','platform' in command,type(command.get('platform')) is str,False)
    if raw.get('account_id')!=bot_account:
        reject('CONTEXT_ACCOUNT_MISMATCH','account_id','account_id' in raw,type(raw.get('account_id')) is str,False)
    if command.get('user_id')!=account:
        reject('CONTEXT_AUTHOR_MISMATCH','author','user_id' in command,type(command.get('user_id')) is str,False)
    if command.get('group_id')!=group_id:
        reject('CONTEXT_GROUP_MISMATCH','group_id','group_id' in command,type(command.get('group_id')) is str,False)
    if raw.get('session_id')!=stream:
        reject('CONTEXT_SESSION_MISMATCH','session_id','session_id' in raw,type(raw.get('session_id')) is str,False)
    if not stream_allowed(stream,plugin.active_config.instance.allowed_streams):
        reject('CONTEXT_STREAM_NOT_ALLOWED','stream_id','stream_id' in command,isinstance(stream,str),False)
    if type(mid) is not str or not 0<len(mid)<=256:
        reject('CONTEXT_MESSAGE_ID_INVALID','message_id','message_id' in raw,type(mid) is str,False)
    if scope is not None and (type(scope) is not str or not 0<len(scope)<=256):
        reject('CONTEXT_SCOPE_INVALID','scope','scope' in raw,type(scope) is str,False)
    for flag in ('is_bot','is_system','is_mai_message','is_notify'):
        if raw.get(flag,False) is not False:
            reject('CONTEXT_FLAG_SET',flag,flag in raw,type(raw.get(flag)) is bool,False)
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
        route=await command_route(plugin.ctx,command)
        if not plugin.ready or plugin.runtime is not runtime or plugin.active_config is not cfg:
            return plugin.error('NOT_READY')
        query,key=local_request(plugin,command,route['account_id'])
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
