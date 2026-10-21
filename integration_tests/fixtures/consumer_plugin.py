from maibot_sdk import API,MaiBotPlugin


class Consumer(MaiBotPlugin):
    def get_default_config(self): return {'plugin':{'enabled':True,'config_version':'1'}}
    async def on_load(self): pass
    async def on_unload(self): pass
    async def on_config_update(self,scope,config_data,version): pass

    @API('call_peer',public=True,version='1')
    async def call_peer(self,route='',args=None,**kwargs):
        return await self.ctx.api.call(route,**(args or {}))


def create_plugin(): return Consumer()
