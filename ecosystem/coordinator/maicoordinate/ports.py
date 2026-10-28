"""SDK 2.15 drops outer success on api.call/list and detailed send.

Use its public raw cap.call entry to preserve both layers. The Runner still
binds the caller and Host still enforces the same manifest capabilities.
"""
class APIPort:
    def __init__(self,ctx): self.ctx=ctx

    async def call(self,api_name,*,version,**args):
        return await self.ctx.call_host_method('cap.call',payload={'capability':'api.call',
            'args':{'api_name':api_name,'version':version,'args':args}},timeout_ms=2000)

    async def list(self,*,plugin_id):
        return await self.ctx.call_host_method('cap.call',payload={'capability':'api.list',
            'args':{'plugin_id':plugin_id}},timeout_ms=2000)


class SendPort:
    def __init__(self,ctx): self.ctx=ctx

    async def text(self,text,stream_id,*,return_details):
        return await self.ctx.call_host_method('cap.call',payload={'capability':'send.text',
            'args':{'text':text,'stream_id':stream_id,'return_details':return_details}},timeout_ms=10000)


class SDKPorts:
    def __init__(self,ctx):
        self.api=APIPort(ctx); self.send=SendPort(ctx); self.llm=ctx.llm
