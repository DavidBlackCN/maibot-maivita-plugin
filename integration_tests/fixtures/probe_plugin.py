"""Synthetic I0 SDK plugin. Loaded only from the temporary fixture directory."""
from pathlib import Path
import asyncio
import importlib.metadata
import json
import os
import sys
import types

from maibot_sdk import API,Command,HookHandler,MaiBotPlugin,Tool

ROOT=Path(os.environ['MAIVITA_I0_SOURCE'])
package=types.ModuleType('maivita_i0_core'); package.__path__=[str(ROOT)]
sys.modules.setdefault(package.__name__,package)
from maivita_i0_core.maivita.compatibility.life_bridge import LegacyLifeBridge
from maivita_i0_core.maivita.life.domain.clock import ManualClock,UTC
from maivita_i0_core.maivita.integrations.life_api import LifeAPI
from maivita_i0_core.maivita.integrations.safety import OwnedFrequency
from maivita_i0_core.maivita.life.life_state.runtime import DevelopmentRuntime
from maivita_i0_core.maivita.life.storage.life_database import LifeDatabase
from datetime import datetime


class Probe(MaiBotPlugin):
    def get_default_config(self):
        return {'plugin':{'enabled':True,'config_version':'1'}}

    async def on_load(self):
        self.data=Path(self.ctx.paths.data_dir)
        self.counter=self.data/'i0-lifecycle.json'
        record=json.loads(self.counter.read_text()) if self.counter.exists() else {'loads':0,'unloads':0}
        record['loads']+=1; self.counter.write_text(json.dumps(record))
        self.config_updates=0
        clock=ManualClock(datetime(2026,10,21,0,tzinfo=UTC))
        path=self.data/'maivita.db'
        db=LifeDatabase(path,clock=clock) if path.exists() else LifeDatabase.create(path,clock=clock)
        async def model(state,triggers):
            response=await self.ctx.llm.generate('I0 synthetic candidate',task_name='utils',rpc_timeout_ms=2000)
            if not response.get('success'): raise ConnectionError('I0 model backend unavailable')
            return json.loads(response['response'])
        self.runtime=DevelopmentRuntime(db,model,lock_path=self.data/'writer.lock').start()
        self.worker=asyncio.create_task(self.runtime.run())
        self.life=LifeAPI(self.runtime)
        self.frequency=OwnedFrequency(self.ctx.frequency)

    async def on_unload(self):
        if hasattr(self,'runtime'): await self.runtime.close()
        if hasattr(self,'counter'):
            record=json.loads(self.counter.read_text()); record['unloads']+=1
            self.counter.write_text(json.dumps(record))

    async def on_config_update(self,scope,config_data,version):
        self.config_updates+=1

    @API('info',version='1',public=True)
    async def info(self,**kwargs):
        return {'sdk':importlib.metadata.version('maibot-plugin-sdk'),
            'paths':{'data':str(self.ctx.paths.data_dir),'runtime':str(self.ctx.paths.runtime_dir)},
            'lifecycle':json.loads(self.counter.read_text()),'worker_active':not self.worker.done(),
            'config_updates':self.config_updates,'state':self.runtime.queries.get_life_state()}

    @API('get_life_state',version='1',public=True)
    async def get_life_state(self,**kwargs):
        return await self.life.call('get_life_state',kwargs,trusted_context=None)

    @API('davidblackcn.maibot-autonomous-planning-plugin.get_current_activity',version='1',public=True)
    async def legacy_alias(self,**kwargs):
        return LegacyLifeBridge(self.runtime.engine).get_current_activity()

    @API('private_probe',version='1',public=False)
    async def private_probe(self,**kwargs): return {'private':True}

    @API('try_write',version='1',public=True)
    async def try_write(self,**kwargs):
        return await self.life.call('request_replan',kwargs,trusted_context=None)

    @Tool('i0_probe',description='I0 contract exercise only')
    async def probe(self,action='',**kwargs):
        if action=='route':
            args=dict(kwargs); args.pop('action',None)
            return await self.ctx.llm.generate('I0 synthetic route',**args)
        if action=='send':
            return await self.ctx.send.text('I0 receipt','synthetic-stream',maivita_receipt_token='synthetic-nonce',source_plugin_id='forged')
        if action=='owned_frequency':
            return {'available':self.frequency.available,'set':await self.frequency.set_adjust('synthetic-stream',0),'remove':await self.frequency.remove_adjust('synthetic-stream')}
        if action=='forged_caller':
            return await self.ctx.call_host_method('cap.call',plugin_id='maivita.i0.consumer',payload={'capability':'database.query','args':{}})
        if action=='raw_bootstrap':
            return await self.ctx.call_host_method('plugin.bootstrap',payload={})
        if action=='deadline_read': return self.runtime.tick()
        if action=='replan': return await self.runtime.request_replan('i0-explicit-request')
        if action=='forbidden_network':
            import socket
            with socket.socket() as connection: connection.connect(('192.0.2.1',443))
        if action=='forbidden_write':
            (ROOT/'I0_FORBIDDEN_WRITE').write_text('must be denied')
        return {'unknown_action':True}

    @Command('i0_status',pattern=r'^/i0-status$')
    async def command(self,**kwargs): return True,'I0 synthetic status',2

    @HookHandler('maisaka.replyer.before_request',name='i0_echo')
    async def hook(self,**kwargs):
        result=dict(kwargs); result['extra_prompt']=result.get('extra_prompt','')+' [i0]'
        return {'action':'modify','modified_kwargs':result}


def create_plugin(): return Probe()
