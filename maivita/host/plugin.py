"""Installable I1 lifecycle adapter. Only the new engine may run here."""
from pathlib import Path
import asyncio
from ..runtime_versions import runtime_pair, runtime_supported
import json
import re

from maibot_sdk import API,Command,HookHandler,MaiBotPlugin,Tool

from ..compatibility.life_bridge import LegacyLifeBridge
from ..integrations.life_api import LifeAPI
from ..integrations.safety import assert_no_old_writer
from ..life.life_state.runtime import DevelopmentRuntime,LifeRuntimeConfig
from ..life.storage.life_database import LifeDatabase
from ..life.storage.safety import WriterLease,resolve_data_dir
from .config import DevelopmentConfig,stream_allowed
from .model_port import SDKModelPort

PLUGIN_ID='davidblackcn.maibot-maivita-plugin'
BEGIN='[MaiVita I1 state]'
END='[/MaiVita I1 state]'


class MaiVitaDevelopmentPlugin(MaiBotPlugin):
    config_model=DevelopmentConfig

    def __init__(self):
        super().__init__()
        self.runtime=None; self.worker=None; self.startup=None; self.deployment_lease=None
        self.api=None; self.model_port=None; self.active_config=None; self.management=None
        self.ready=False; self.failure_code=None
        self.startup_result={'status':'not_requested'}; self.guarded_streams=()

    def normalize_plugin_config(self,config_data):
        if config_data: DevelopmentConfig.model_validate(config_data)
        return super().normalize_plugin_config(config_data)

    async def on_load(self):
        if self.runtime is not None or self.deployment_lease is not None:
            raise RuntimeError('I1 lifecycle already active')
        cfg=self.config
        if not cfg.plugin.enabled: return
        try:
            if not runtime_supported():
                raise RuntimeError('I1 unsupported Host/SDK pair: '+repr(runtime_pair()))
            await assert_no_old_writer(self.ctx)
            data=resolve_data_dir(self.ctx)
            # Refuse legacy co-location before opening any database. No import
            # or migration of old data is part of this entry.
            if (data/'goals.db').exists(): raise RuntimeError('I1 refuses a directory containing goals.db')
            self.deployment_lease=WriterLease(Path(cfg.instance.writer_lock) if cfg.instance.writer_lock else data/'deployment-writer.lock')
            self.deployment_lease.acquire()
            data.mkdir(parents=True,exist_ok=True)
            db_path=data/'maivita.db'
            db=LifeDatabase(db_path) if db_path.exists() else LifeDatabase.create(db_path)
            self.active_config=cfg.model_copy(deep=True)
            self.guarded_streams=tuple(cfg.instance.allowed_streams)
            def context():
                return {'now':db.clock.now_utc(),'state':self.runtime.queries.get_life_state(),
                    'character_timezone':cfg.instance.timezone,
                    'local_time':db.clock.to_local(db.clock.now_utc(),cfg.instance.timezone).isoformat(),
                    'plan':self.runtime.queries.get_plan(),'goals':self.runtime.queries.get_goals(),
                    'segment':self.runtime.segments.context() if cfg.scheduler.enabled else None}
            self.model_port=SDKModelPort(self.ctx,cfg.model,context=context,routine=cfg.routine)
            self.runtime=DevelopmentRuntime(db,self.model_port,config=LifeRuntimeConfig(timezone=cfg.instance.timezone,
                model_timeout_seconds=cfg.model.timeout_seconds,daily_planning_time=cfg.model.daily_planning_time,
                daily_budget=cfg.model.daily_budget,daily_planning_enabled=cfg.model.daily_planning_enabled,
                scheduler=cfg.scheduler.runtime_config()),lock_path=data/'runtime-writer.lock')
            self.runtime.start()
            self.api=LifeAPI(self.runtime)  # no trusted end-user contract: privileged writes denied
            self.worker=asyncio.create_task(self.runtime.run(),name='maivita-i1-worker')
            await asyncio.sleep(0)
            if self.worker.done(): await self.worker
            self.worker.add_done_callback(self._worker_stopped)
            self.ready=True; self.failure_code=None
            if cfg.model.generate_on_start and not cfg.model.daily_planning_enabled:
                self.startup=asyncio.create_task(self.runtime.request_replan('i1-configured-startup'),name='maivita-i1-startup')
                self.startup.add_done_callback(self._startup_stopped)
        except BaseException:
            await self.on_unload()
            raise

    def _worker_stopped(self,task):
        if not task.cancelled() and task.exception() is not None:
            self.ready=False; self.failure_code='RUNTIME_FAILED'
            # Keep the deployment lease until explicit unload: a second engine
            # must not take over while failed lifecycle state is still loaded.

    def _startup_stopped(self,task):
        if not task.cancelled() and task.exception() is not None:
            self.failure_code='STARTUP_DECISION_FAILED'
            self.startup_result={'status':'failed'}
        elif not task.cancelled():
            self.startup_result=task.result()

    async def on_unload(self):
        self.ready=False
        try:
            if self.startup is not None:
                self.startup.cancel(); await asyncio.gather(self.startup,return_exceptions=True)
            if self.runtime is not None: await self.runtime.close()
            if self.worker is not None:
                self.worker.cancel(); await asyncio.gather(self.worker,return_exceptions=True)
        finally:
            if self.management is not None: self.management.close(); self.management=None
            if self.deployment_lease is not None: self.deployment_lease.release()
            self.runtime=None; self.worker=None; self.startup=None; self.api=None
            self.model_port=None; self.deployment_lease=None; self.active_config=None

    async def on_config_update(self,scope,config_data,version):
        if scope!='self': return
        new=DevelopmentConfig.model_validate(config_data)
        if not new.plugin.enabled:
            await self.on_unload()
        elif self.active_config is None or new!=self.active_config:
            # Reject in-place changes of identity, paths, streams, timezone or
            # model basis. Host reload must stop the old worker first.
            await self.on_unload()
            self.failure_code='RELOAD_REQUIRED'
            raise RuntimeError('I1 configuration changed; reload plugin to activate safely')

    def error(self,code='NOT_READY'):
        return {'api_version':1,'ok':False,'data':None,'error':{'code':code,'message':code,'retryable':False},
            'state_version':None,'as_of':None}

    async def read(self,method):
        if not self.ready: return self.error(self.failure_code or 'NOT_READY')
        result=await self.api.call(method,trusted_context=None)
        if method=='get_diagnostics' and result['ok']:
            result['data'].update({'entry':'life','host_contract':'/'.join(str(v) for v in runtime_pair()),
                'frequency':'disabled_unsupported','owner_auth':'disabled_no_contract',
                'privileged_writes':'denied_no_trusted_user_context','trusted_receipts':'no_silence_exception',
                'model':self.model_port.last,'startup_decision':self.startup_result,'local_management':'official_command_with_confirmation','first_install_gate':'see_source_bound_delivery_evidence'})
        return result

    @API('get_life_state',public=True,version='1')
    async def get_life_state(self,**kwargs): return await self.read('get_life_state')
    @API('get_plan',public=True,version='1')
    async def get_plan(self,**kwargs): return await self.read('get_plan')
    @API('get_availability',public=True,version='1')
    async def get_availability(self,**kwargs): return await self.read('get_availability')
    @API('get_diagnostics',public=True,version='1')
    async def get_diagnostics(self,**kwargs): return await self.read('get_diagnostics')
    @API('get_current_activity',public=True,version='1')
    async def get_current_activity(self,**kwargs):
        if not self.ready: return {'has_activity':False,'activity':None,'error':'NOT_READY'}
        return LegacyLifeBridge(self.runtime.engine,timezone=self.active_config.instance.timezone).get_current_activity()
    @API('davidblackcn.maibot-autonomous-planning-plugin.get_current_activity',public=True,version='1')
    async def old_current_activity(self,**kwargs): return await self.get_current_activity()
    @API('request_replan',public=True,version='1')
    async def request_replan(self,**kwargs): return self.error('PERMISSION_DENIED')
    @API('request_wakeup',public=True,version='1')
    async def request_wakeup(self,**kwargs): return self.error('POLICY_UNAVAILABLE')

    @Tool('maivita_status',description='Read public MaiVita state, plan or availability; never wakes or writes')
    async def status_tool(self,view='state',**kwargs):
        method={'state':'get_life_state','plan':'get_plan','availability':'get_availability'}.get(view)
        return await self.read(method) if method else self.error('INVALID_ARGUMENT')
    @Tool('get_planning_status_v4',description='Read the new engine public plan through the old Tool name')
    async def old_status_tool(self,**kwargs): return await self.read('get_plan')
    @Tool('generate_schedule_v4',description='Unavailable: trusted management identity contract required')
    async def old_generate_tool(self,**kwargs): return self.error('PERMISSION_DENIED')
    @Tool('apply_schedule_v4',description='Unavailable: trusted management identity contract required')
    async def old_apply_tool(self,**kwargs): return self.error('PERMISSION_DENIED')
    @Tool('manage_goal_v4',description='Read public goals; mutations require an unavailable trusted identity contract')
    async def old_goal_tool(self,action='list',**kwargs):
        return await self.read('get_goals') if action=='list' else self.error('PERMISSION_DENIED')

    @Command('maivita',pattern=r'^(?:/(?:maivita|plan|规划)|麦麦管理)(?:\s|$)')
    async def command(self,text='',message=None,stream_id='',**kwargs):
        # Read-only commands have no Owner exemption and use the normal send
        # capability. Sleeping commands never receive a send permit.
        if not self.ready or not stream_allowed(stream_id,self.active_config.instance.allowed_streams):
            return True,'MaiVita not ready or test stream not allowed',2
        runtime=self.runtime
        raw=text or (message if isinstance(message,str) else '')
        action=raw.strip().split(maxsplit=1)[1] if len(raw.strip().split(maxsplit=1))==2 else 'status'
        method={'status':'get_life_state','plan':'get_plan','availability':'get_availability','diagnostics':'get_diagnostics','goals':'get_goals','activity':'get_life_state'}.get(action)
        from .management import ACTIONS,execute
        rendered=None
        if action=='help':
            from .life_commands import HELP
            rendered=HELP;result={'ok':True,'data':{}}
        elif action in ACTIONS and raw.strip().startswith('/maivita '):
            result=await execute(self,{**kwargs,'text':text,'stream_id':stream_id,'message':message})
        elif action=='status' and raw.strip()=='/maivita status':
            from .command_status import status_text
            rendered=await status_text(self,stream_id)
            result={'ok':True,'data':{}}
        elif method:
            result=await self.read(method)
        else:
            from .management_requests import dispatch
            rendered=await dispatch(self,{**kwargs,'text':text,'stream_id':stream_id,'message':message})
            result={'ok':True,'data':{}}
        if not self.ready or self.runtime is not runtime:
            return True,'MaiVita reloaded during command; no receipt sent',2
        if runtime.engine.state().sleep_phase in ('sleeping','temporarily_awake'):
            return True,'MaiVita sleeping; no trusted management receipt exception',2
        from .presentation import public_text
        if await self.ctx.send.text(rendered if rendered is not None else public_text(action,result,self.active_config.instance.timezone),stream_id) is not True:
            return True,'MaiVita normal send rejected or unavailable; no bypass attempted',2
        return True,'MaiVita public result sent through normal permissions',2

    def inject(self,kwargs):
        if not self.ready or not stream_allowed(kwargs.get('session_id'),self.active_config.instance.allowed_streams):
            return {'action':'continue'}
        original=kwargs.get('extra_prompt','')
        if not isinstance(original,str): return {'action':'continue'}
        # Own marked block only. Unknown fields and other plugin text survive.
        original=re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END),'',original,flags=re.S).rstrip()
        state=self.runtime.queries.get_life_state()
        block=BEGIN+'\n'+json.dumps(state,ensure_ascii=False)+'\n'+END
        return {'action':'continue','modified_kwargs':{**kwargs,'extra_prompt':original+'\n'+block}}

    @HookHandler('maisaka.replyer.before_request',name='maivita_life_replyer')
    async def replyer_hook(self,**kwargs): return self.inject(kwargs)
    @HookHandler('maisaka.planner.before_request',name='maivita_life_planner')
    async def planner_hook(self,**kwargs):
        if 'extra_prompt' in kwargs:return self.inject(kwargs)
        if not self.ready or not stream_allowed(kwargs.get('session_id'),self.active_config.instance.allowed_streams):return {'action':'continue'}
        from .typed_prompt import inject
        return inject(self,kwargs,self.runtime.queries.get_life_state(),'MaiVita')
    @HookHandler('send_service.before_send',name='maivita_sleep_silence',order='early',error_policy='abort')
    async def silence_hook(self,message=None,**kwargs):
        stream=message.get('session_id') if isinstance(message,dict) else getattr(message,'session_id',None)
        if not self.ready:
            return ({'action':'abort','reason':'maivita_runtime_failed'} if self.failure_code and
                (not stream or stream_allowed(stream,self.guarded_streams)) else {'action':'continue'})
        # Host message serialization carries session_id. Unknown identity while
        # asleep cannot establish a trusted exception.
        phase=self.runtime.engine.state().sleep_phase
        if phase in ('sleeping','temporarily_awake') and (not stream or stream_allowed(stream,self.active_config.instance.allowed_streams)):
            return {'action':'abort','reason':'maivita_sleeping_no_receipt_exception'}
        return {'action':'continue'}


def create_plugin(): return MaiVitaDevelopmentPlugin()
