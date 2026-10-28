import asyncio
import unittest
from copy import deepcopy
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import r0_support
from maibot_autonomous_planning_plugin.maivita.host.management import execute,local_request,API

def command(user='10001'):
    return {'text':'/maivita wake','user_id':user,'stream_id':'s','platform':'qq','group_id':'20001',
        'message':{'platform':'qq','account_id':'99999','scope':None,'session_id':'s','message_id':'m1',
            'processed_plain_text':'/maivita wake','message_info':{'user_info':{'user_id':user},'group_info':{'group_id':'20001'}}}}

def routes():
    return [{'session_id':'s','stream_id':'s','platform':'qq','account_id':'99999',
             'scope':None,'is_group_session':True,'group_id':'20001'}]

class ManagementTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self,response=None):
        engine=SimpleNamespace(state=lambda:SimpleNamespace(state_version=1,config_revision=1),
            execute=Mock(return_value={'event_id':'e1'}),clock=SimpleNamespace(now_utc=lambda:datetime.now(timezone.utc)))
        return SimpleNamespace(ready=True,runtime=SimpleNamespace(engine=engine),
            active_config=SimpleNamespace(instance=SimpleNamespace(allowed_streams=['s'])),
            ctx=SimpleNamespace(config=SimpleNamespace(get=AsyncMock(return_value='')),
                chat=SimpleNamespace(get_all_streams=AsyncMock(side_effect=lambda **kw:routes())),
                call_host_method=AsyncMock(return_value=response or {'success':True,'result':{'policy_version':1,'allowed':True}})),
            error=lambda code:{'ok':False,'error':{'code':code}})

    async def test_official_actor_used_not_supplied_owner_and_local_key(self):
        p=self.plugin();c=command();c.update(owner=True,actor={'account_id':'10002'},__maibot_command_ref__='fake')
        self.assertTrue((await execute(p,c))['ok'])
        query=p.ctx.call_host_method.call_args.kwargs['payload']['args']
        self.assertEqual(query['api_name'],API)
        self.assertEqual(query['args']['account_id'],'10001')
        self.assertEqual(set(query['args']),{'account_id','bot_account_id','route_scope','stream_id','action'})
        first=p.runtime.engine.execute.call_args.args[0]
        self.assertEqual(first.kind,'wake');self.assertTrue(first.idempotency_key.startswith('local-command:'))
        await execute(p,c)
        self.assertEqual(first.idempotency_key,p.runtime.engine.execute.call_args.args[0].idempotency_key)
        p.ctx.config.get.assert_not_awaited()

    async def test_adapter_route_overrides_empty_or_stale_fallback(self):
        for fallback in ('','88888'):
            p=self.plugin();p.ctx.config.get.return_value=fallback
            self.assertTrue((await execute(p,command()))['ok'])
            p.ctx.config.get.assert_not_awaited()
        for rows in ([],routes()+routes(),[dict(routes()[0],account_id='88888')],
                     [dict(routes()[0],group_id='20002')]):
            p=self.plugin();p.ctx.chat.get_all_streams.side_effect=None
            p.ctx.chat.get_all_streams.return_value=rows
            self.assertFalse((await execute(p,command()))['ok'])
            p.runtime.engine.execute.assert_not_called()

    async def test_reload_or_cancellation_during_route_lookup_never_writes(self):
        p=self.plugin()
        async def changed(**kw):
            p.active_config=deepcopy(p.active_config)
            return routes()
        p.ctx.chat.get_all_streams.side_effect=changed
        self.assertFalse((await execute(p,command()))['ok'])
        p.runtime.engine.execute.assert_not_called()
        p=self.plugin();p.ctx.chat.get_all_streams.side_effect=asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):await execute(p,command())
        p.runtime.engine.execute.assert_not_called()

    async def test_malformed_envelopes_never_query_or_mutate(self):
        variants=[]
        for key,value in [('user_id','10002'),('stream_id','other'),('group_id','20002'),('platform','webui'),('text','/plan wake')]:
            variants.append({**command(),key:value})
        for key,value in [('is_bot',True),('is_system',True),('is_mai_message',True),('is_notify',True),
                          ('account_id','88888'),('message_id',''),('scope',''),('processed_plain_text','/maivita replan')]:
            c=command();c['message'][key]=value;variants.append(c)
        variants.extend([command('99999'),{'__maibot_command_ref__':'fake'}])
        for c in variants:
            p=self.plugin()
            self.assertFalse((await execute(p,c))['ok'],c)
            p.ctx.call_host_method.assert_not_awaited();p.runtime.engine.execute.assert_not_called()

    async def test_denied_unavailable_and_wrong_policy_version_never_mutate(self):
        for response in [{'success':False},{'success':True,'result':{'allowed':True}},
            {'success':True,'result':{'policy_version':2,'allowed':True}},
            {'success':True,'result':{'policy_version':1,'allowed':False}}]:
            p=self.plugin(response)
            self.assertFalse((await execute(p,command()))['ok']);p.runtime.engine.execute.assert_not_called()
        for error in (RuntimeError('missing Identity'),asyncio.TimeoutError()):
            p=self.plugin();p.ctx.call_host_method.side_effect=error
            self.assertFalse((await execute(p,command()))['ok']);p.runtime.engine.execute.assert_not_called()

    async def test_unload_config_change_and_cancellation_never_mutate(self):
        for change in ('unload','config'):
            p=self.plugin()
            async def update(*a,**k):
                if change=='unload': p.ready=False
                else: p.active_config=deepcopy(p.active_config)
                return {'success':True,'result':{'policy_version':1,'allowed':True}}
            p.ctx.call_host_method.side_effect=update
            self.assertFalse((await execute(p,command()))['ok']);p.runtime.engine.execute.assert_not_called()
        p=self.plugin();p.ctx.call_host_method.side_effect=asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError): await execute(p,command())
        p.runtime.engine.execute.assert_not_called()

    async def test_outer_command_reload_returns_without_accessing_closed_runtime(self):
        from maibot_autonomous_planning_plugin.maivita.host.plugin import MaiVitaDevelopmentPlugin
        p=self.plugin();engine=p.runtime.engine
        async def unload(*a,**k):
            p.ready=False;p.runtime=None;p.active_config=None
            return {'success':True,'result':{'policy_version':1,'allowed':True}}
        p.ctx.call_host_method.side_effect=unload
        result=await MaiVitaDevelopmentPlugin.command(p,**command())
        self.assertIn('reloaded',result[1]);engine.execute.assert_not_called()

    def test_contract_declares_no_private_host_dependency(self):
        from maibot_autonomous_planning_plugin.maivita.integrations.contracts import contract_descriptor
        contract=contract_descriptor()
        self.assertNotIn('security.resolve_command',str(contract))
        self.assertFalse(contract['local_command_adapter']['host_patch_required'])
        self.assertFalse(contract['local_command_adapter']['policy_result_is_execution_token'])
