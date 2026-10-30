"""TEST ONLY: staged Runner model/platform replacements, never in release archives."""
import asyncio
import json
from types import SimpleNamespace


def install():
    from .services import feed_publish,monitor
    class Qzone:
        uin='123456789'
        def __init__(self,plugin):self.plugin=plugin
        def record(self,kind):
            path=self.plugin.ctx.paths.data_dir/'native-space-calls.jsonl'
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('a',encoding='utf-8') as f:f.write(json.dumps({'kind':kind})+'\n')
        async def get_send_history(self,*args,**kwargs):return ''
        async def publish_emotion(self,story,images,publication=None):
            if publication:publication.stage='submitting'
            self.record('submit')
            await asyncio.sleep(3.2)  # Proves the obsolete 2s RPC limit is gone.
            if publication:publication.stage='confirmed';publication.tid='native-space-tid'
            self.record('published')
            return 'native-space-tid'
        async def monitor_get_list(self,*args,**kwargs):
            self.record('read')
            return [{'tid':'friend','target_qq':'10001','content':'synthetic public feed'},
                {'tid':'self','target_qq':self.uin,'content':'synthetic own feed','comments':[
                    {'qq_account':'10001','comment_tid':'allowed','content':'synthetic reply'},
                    {'qq_account':'10009','comment_tid':'forbidden','content':'excluded'}]}]
        async def like(self,*args,**kwargs):self.record('like');return True
        async def comment(self,*args,**kwargs):self.record('comment');return True
        async def reply(self,*args,**kwargs):
            assert args[4]=='allowed'
            self.record('reply');return True
    async def publisher(plugin):return Qzone(plugin)
    async def persona(plugin):return SimpleNamespace(personality='quiet',style='plain',self_description='',reference_image_path='')
    feed_publish.create_qzone_publisher=publisher
    monitor.make_qzone_api=publisher
    feed_publish.resolve_persona=persona
    monitor.resolve_persona=persona
