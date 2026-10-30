"""Optional one-shot server acceptance Runner. Local config is the only write control."""
import asyncio,json
from pathlib import Path
from types import SimpleNamespace
from maibot_sdk import MaiBotPlugin,PluginConfigBase
from pydantic import Field
from .server_acceptance import run


class PluginSettings(PluginConfigBase):
    enabled:bool=True
    config_version:str='1'


class ProbeConfig(PluginConfigBase):
    plugin:PluginSettings=Field(default_factory=PluginSettings)
    execute:bool=False
    operation_id:str='readonly'
    publish_account:str=''
    interaction_target:str=''


class Probe(MaiBotPlugin):
    config_model=ProbeConfig
    def __init__(self):super().__init__();self.task=None
    async def on_load(self):
        if self.config.plugin.enabled:self.task=asyncio.create_task(self.check())
    async def check(self):
        data=self.ctx.paths.data_dir;data.mkdir(parents=True,exist_ok=True)
        operation=self.config.operation_id
        import hashlib
        receipt=data/(hashlib.sha256(operation.encode()).hexdigest()+'.json')
        if self.config.execute and receipt.exists():return  # Never replay uncertain tests.
        def write(value):
            temporary=receipt.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(receipt)
        if self.config.execute:write({'status':'submitted','operation':operation,'restart_rule':'do_not_retry'})
        ctx=self.ctx
        class ExistingHost:
            async def invoke_api(self,plugin_id,name,args,timeout_ms):
                response=await ctx.call_host_method('cap.call',payload={'capability':'api.call',
                    'args':{'api_name':plugin_id+'.'+name,'version':'1','args':args}},timeout_ms=timeout_ms)
                return SimpleNamespace(error=None,payload=response)
        try:
            result=await run(ExistingHost(),execute=self.config.execute,publish_account=self.config.publish_account or None,
                interaction_target=self.config.interaction_target or None)
            write({'status':'finished','operation':operation,'result':result})
        except asyncio.CancelledError:
            if self.config.execute:write({'status':'unknown','operation':operation,'reason':'unloaded; check QQ before another test'})
            raise
        except Exception as exc:write({'status':'unknown' if self.config.execute else 'failed','operation':operation,'reason':str(exc)})
    async def on_unload(self):
        if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True);self.task=None
    async def on_config_update(self,scope,config_data,version):
        if scope!='self':return
        ProbeConfig.model_validate(config_data)
        await self.on_unload()
        raise RuntimeError('RELOAD_REQUIRED')


def create_plugin():return Probe()
