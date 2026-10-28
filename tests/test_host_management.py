import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import r0_support
from maibot_autonomous_planning_plugin.maivita.host.management import execute

class ManagementTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self,response):
        engine=SimpleNamespace(state=lambda:SimpleNamespace(state_version=1,config_revision=1),execute=Mock())
        return SimpleNamespace(ready=True,runtime=SimpleNamespace(engine=engine),
            ctx=SimpleNamespace(call_host_method=AsyncMock(return_value=response)),
            error=lambda code:{'ok':False,'error':{'code':code}})

    async def test_denied_unavailable_wrong_action_stream_and_key_never_mutate(self):
        allowed={'allowed':True,'action':'wake','stream_id':'s','request_key':'host-command:'+'a'*64}
        responses=[None,{'success':False},{'success':True,'result':{'allowed':False}},
            *[{'success':True,'result':{**allowed,field:value}} for field,value in
              [('action','configure'),('stream_id','other'),('request_key','fake')]]]
        for response in responses:
            p=self.plugin(response)
            self.assertFalse((await execute(p,'wake','s','opaque'))['ok'])
            p.runtime.engine.execute.assert_not_called()
        p=self.plugin({});self.assertFalse((await execute(p,'wake','s',None))['ok'])
        p.ctx.call_host_method.assert_not_awaited()

    async def test_unload_during_authorization_never_mutates(self):
        p=self.plugin(None)
        async def unload(*a,**k):
            p.ready=False
            return {'success':True,'result':{'allowed':True,'action':'wake','stream_id':'s','request_key':'host-command:'+'a'*64}}
        p.ctx.call_host_method.side_effect=unload
        self.assertFalse((await execute(p,'wake','s','opaque'))['ok'])
        p.runtime.engine.execute.assert_not_called()
