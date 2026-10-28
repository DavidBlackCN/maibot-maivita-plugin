import asyncio
import importlib.metadata
import os
from maibot_sdk import API, Command, HookHandler, MaiBotPlugin
from .config import Settings, HOST, SDK, SUPPORTED_PAIRS, Route, HUSH
from .contracts import decide, Unavailable
from .service import Coordinator
from .store import Store
from .ports import SDKPorts


class MaiCoordinatePlugin(MaiBotPlugin):
    config_model=Settings

    @classmethod
    def build_config_schema(cls,**metadata):
        from .schema import build_schema
        return build_schema(**metadata)

    def __init__(self):
        super().__init__()
        self.service=None; self.store=None; self.worker=None; self.active=None
        self.observed={}; self.automatic=False

    def normalize_plugin_config(self,config_data):
        Settings.model_validate(config_data or {})
        return super().normalize_plugin_config(config_data)

    async def on_load(self):
        if not self.config.plugin.enabled: return
        if self.store is not None: raise RuntimeError('ALREADY_LOADED')
        if (os.environ.get('MAIBOT_HOST_VERSION'),importlib.metadata.version('maibot-plugin-sdk')) not in SUPPORTED_PAIRS:
            raise RuntimeError('UNSUPPORTED_BASELINE')
        self.active=self.config.model_copy(deep=True)
        try:
            if self.active.mode=='controlled':
                tasks=await asyncio.wait_for(self.ctx.llm.get_available_models(),2)
                if self.active.model_task not in tasks: raise ValueError('MODEL_TASK_UNAVAILABLE')
            self.store=Store(self.ctx.paths.data_dir/'coordinate.sqlite3',self.active)
            self.automatic=not self.active.routes
            if self.automatic:
                saved=[Route.model_validate(v) for v in self.store.get('observed_routes',[])]
                if len(saved)>4: raise ValueError('TOO_MANY_STORED_ROUTES')
                self.active=self.active.model_copy(update={'routes':saved})
            self.service=Coordinator(SDKPorts(self.ctx),self.active,self.store)
            self.worker=asyncio.create_task(self.run())
        except BaseException:
            await self.on_unload(); raise

    async def run(self):
        while True:
            try:
                await self.discover_observed()
                if self.active.routes: await self.service.cycle()
                else: self.service.health='WAITING_FOR_MANAGED_GROUP'
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.service.health='UNAVAILABLE'
                self.service.last_error=str(exc) if isinstance(exc,Unavailable) else type(exc).__name__
            await asyncio.sleep(self.active.poll_seconds)

    @HookHandler('chat.receive.after_process',name='maicoordinate_observe_route')
    async def observe_route(self,message=None,**kwargs):
        # Host callback only. Bounded admission; RPC happens in our worker, not
        # inside the receive Hook deadline. Never uses claimed owner/permissions.
        if not self.automatic or not self.service or not isinstance(message,dict): return None
        stream=message.get('session_id'); info=message.get('message_info')
        group=info.get('group_info') if isinstance(info,dict) else None
        if message.get('platform')!='qq' or not isinstance(stream,str) or not 1<=len(stream)<=256 or not isinstance(group,dict): return None
        account=message.get('account_id'); scope=message.get('scope'); gid=group.get('group_id')
        if not isinstance(account,str) or not account.isascii() or not account.isdigit() or not isinstance(gid,str) or not gid.isascii() or not gid.isdigit(): return None
        if scope is not None and (not isinstance(scope,str) or not scope or len(scope)>256): return None
        if any(r.stream_id==stream for r in self.active.routes): return None
        if len(self.active.routes)+len(self.observed)>=4 or stream in self.observed: return None
        self.observed[stream]=(account,scope,gid)
        return None

    async def discover_observed(self):
        from .contracts import envelope
        import time
        while self.observed and len(self.active.routes)<4:
            stream,(account,scope,gid)=self.observed.popitem()
            result=envelope(await self.service.call(HUSH,'get_public_route_binding',stream_id=stream))
            if (result.get('platform'),result.get('bot_account_id'),result.get('route_scope'),result.get('group_id'),result.get('stream_id'))!=('qq',account,scope or '',gid,stream):
                raise Unavailable('OBSERVED_ROUTE_MISMATCH')
            route=Route(route_id=stream,instance_id='host-managed',character_id='default',
                adapter_namespace=result['adapter_namespace'],bot_account_id=account,
                route_scope=scope or '',group_id=gid,stream_id=stream)
            with self.store.conn:
                routes=[*self.active.routes,route]
                self.store.put('observed_routes',[r.model_dump() for r in routes])
                self.store.put('route_since:'+route.route_id,time.time())
            self.active=self.active.model_copy(update={'routes':routes})
            self.service.settings=self.active

    @Command('maicoordinate',pattern=r'^/maicoordinate(?:\s+status)?\s*$')
    async def status_command(self,stream_id='',**kwargs):
        if stream_id:
            text=('MaiCoordinate：'+(self.active.mode if self.active else '未启用')+
                  '；'+(self.service.health if self.service else 'NOT_READY')+
                  '；已核对群数 '+str(len(self.active.routes) if self.active else 0))
            await self.ctx.send.text(text,stream_id)
        return True,'status handled through normal send',2

    async def on_unload(self):
        if self.worker:
            self.worker.cancel(); await asyncio.gather(self.worker,return_exceptions=True); self.worker=None
        if self.store: self.store.close(); self.store=None
        self.service=None; self.active=None
        self.observed.clear(); self.automatic=False

    async def on_config_update(self,scope,config_data,version):
        if scope!='self': return
        await self.on_unload()
        Settings.model_validate(config_data)
        raise RuntimeError('RELOAD_REQUIRED')

    @API('get_status',public=True,version='1')
    async def get_status(self,**kwargs):
        counts=dict(self.store.conn.execute('SELECT status,count(*) FROM actions GROUP BY status')) if self.store else {}
        return {'api_version':1,'ok':True,'data':{'mode':self.active.mode if self.active else 'disabled',
            'health':self.service.health if self.service else 'NOT_READY','counts':counts,
            'last_error':self.service.last_error if self.service else None,
            'storage_limit':self.active.max_records if self.active else None,
            'payload_retention_days':self.active.payload_retention_days if self.active else None,
            'host':HOST,'sdk':SDK,'H01':False,'H02':False,'H03':False,
            'send_guarantee':'registered_guards_on_SendService_only'},'error':None}

    @API('get_snapshot',public=True,version='1')
    async def get_snapshot(self,route_id=None,**kwargs):
        service=self.service
        if service:
            route=next((r for r in self.active.routes if r.route_id==route_id),None)
            if route:
                snapshot=await service.snapshot(route)
                return {'api_version':1,'ok':True,'data':{'snapshot':snapshot,
                    'decision':decide(snapshot,service.wall()),'send_permit':False},'error':None}
        return {'api_version':1,'ok':False,'data':None,'error':{'code':'ROUTE_UNAVAILABLE','retryable':False}}

    @API('get_action_result',public=True,version='1')
    async def get_action_result(self,route_id=None,event_id=None,**kwargs):
        if (self.store and type(event_id) is str and 1<=len(event_id)<=128
            and any(r.route_id==route_id for r in self.active.routes)):
            row=self.store.conn.execute('SELECT status,reason,basis,attempt,updated FROM actions WHERE route=? AND event_id=?',
                (route_id,event_id)).fetchone()
            if row:
                import json
                return {'api_version':1,'ok':True,'data':{'event_id':event_id,'route_id':route_id,
                    'status':row['status'],'reason':row['reason'],'basis':json.loads(row['basis']),
                    'attempt_id':row['attempt'],'updated_at':row['updated']},'error':None}
        return {'api_version':1,'ok':False,'data':None,'error':{'code':'NOT_FOUND','retryable':False}}


def create_plugin(): return MaiCoordinatePlugin()
