"""Real Host RPC/authorization/registries + real Runner, offline business edges.

Run in a disposable instance directory. This does not start bot.py, the Host
supervisor, platform drivers, provider clients, or a production scheduler.
"""
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType,SimpleNamespace
import asyncio
import importlib.metadata
import json
import os
import secrets
import socket
import sys
import time

from .guard import child_environment,install_guard
from .source import COMMIT,HOST,ROOT,verify_source

PROBE='maivita.i0.probe'
CONSUMER='maivita.i0.consumer'


def business_edges():
    """Explicit stand-ins only at configuration/provider/platform boundaries."""
    traces={'llm':[],'send_service':[],'frequency':[]}
    config=ModuleType('src.config.config'); config.global_config=SimpleNamespace()
    sys.modules[config.__name__]=config
    services=ModuleType('src.services'); services.__path__=[]
    sys.modules[services.__name__]=services
    llm=ModuleType('src.services.llm_service')
    @dataclass
    class Request:
        task_name:str
        request_type:str
        prompt:object
        model_name:object=None
        temperature:object=None
        max_tokens:object=None
    llm.LLMServiceRequest=Request
    llm.get_available_models=lambda:{'utils':object(),'replyer':object()}
    def resolve_task(name):
        if name not in ('utils','replyer'): raise ValueError('I0 unknown task')
        return name
    llm.resolve_task_name=resolve_task
    async def generate(request):
        trace={'task_name':request.task_name,'model_name':request.model_name,'request_type':request.request_type}
        traces['llm'].append(trace)
        return SimpleNamespace(to_capability_payload=lambda:{'success':True,'response':json.dumps({'kind':'winding_down','payload':{'reason':'I0 model fixture'}}),
            'model_name':request.model_name or 'fixture-'+request.task_name,'route':trace})
    llm.generate=generate; services.llm_service=llm; sys.modules[llm.__name__]=llm
    send=ModuleType('src.services.send_service')
    async def send_text(**kwargs):
        traces['send_service'].append({'keys':sorted(kwargs),'stream_id':kwargs['stream_id']})
        return None
    send.text_to_stream_with_message=send_text
    services.send_service=send; sys.modules[send.__name__]=send
    for name in ('src.chat','src.chat.heart_flow'):
        module=ModuleType(name); module.__path__=[]; sys.modules[name]=module
    frequency=ModuleType('src.chat.heart_flow.heartflow_manager')
    frequency.heartflow_manager=SimpleNamespace(adjust_talk_frequency=lambda chat,value:traces['frequency'].append({'chat_id':chat,'value':value}))
    sys.modules[frequency.__name__]=frequency
    return traces


async def run(work:Path):
    verify_source()
    # A proactor's internal socketpair is created before installing the guard.
    install_guard(work,HOST,ROOT,allow_loopback_bind=True)
    from src.plugin_runtime.host.api_registry import APIRegistry
    from src.plugin_runtime.host.authorization import AuthorizationManager
    from src.plugin_runtime.host.capability_service import CapabilityService
    from src.plugin_runtime.host.component_registry import ComponentRegistry
    from src.plugin_runtime.host.rpc_server import RPCServer
    from src.plugin_runtime.protocol.envelope import BootstrapPluginPayload,RegisterPluginPayload
    from src.plugin_runtime.transport.tcp import TCPTransportServer
    traces=business_edges()
    from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin
    from src.plugin_runtime.capabilities.data import RuntimeDataCapabilityMixin
    from src.plugin_runtime.capabilities.components import RuntimeComponentCapabilityMixin
    authorization=AuthorizationManager()
    service=CapabilityService(authorization)
    apis=APIRegistry(); components=ComponentRegistry()
    transport=TCPTransportServer('127.0.0.1',0)
    rpc=RPCServer(transport,session_token=secrets.token_hex(32),host_version='1.3.12')
    allowed={PROBE:{'llm.generate','send.text','frequency.set_adjust'},CONSUMER:{'api.call','database.query'}}
    ready=asyncio.Event(); registrations={}; revoked=[]; capability_calls=[]; steps=[]; log_records=[]
    class Capabilities(RuntimeCoreCapabilityMixin,RuntimeDataCapabilityMixin,RuntimeComponentCapabilityMixin):
        @property
        def supervisors(self): return [supervisor]
    manager=Capabilities()
    async def invoke_api(plugin_id,component_name,args,timeout_ms):
        return await rpc.send_request('plugin.invoke_api',plugin_id,{'component_name':component_name,'args':args},timeout_ms=timeout_ms)
    supervisor=SimpleNamespace(api_registry=apis,component_registry=components,invoke_api=invoke_api)
    async def bootstrap(envelope):
        payload=BootstrapPluginPayload.model_validate(envelope.payload)
        if payload.plugin_id!=envelope.plugin_id or not set(payload.capabilities_required)<=allowed.get(payload.plugin_id,set()):
            return envelope.make_error_response('I0_DECLARATION_DENIED','fixture declaration is not in the configured allowlist')
        if payload.capabilities_required:
            authorization.register_plugin(payload.plugin_id,payload.capabilities_required)
        else:
            authorization.revoke_permission_token(payload.plugin_id)
        return envelope.make_response({'accepted':True})
    async def register(envelope):
        payload=RegisterPluginPayload.model_validate(envelope.payload)
        if payload.plugin_id!=envelope.plugin_id or payload.plugin_id not in allowed:
            return envelope.make_error_response('I0_REGISTRATION_DENIED','unexpected fixture')
        declarations=[item.model_dump() for item in payload.components]
        apis.remove_apis_by_plugin(payload.plugin_id)
        components.remove_components_by_plugin(payload.plugin_id)
        for declaration in declarations:
            if declaration['component_type'].upper()!='API':
                components.register_component(**{k:declaration[k] for k in ('name','component_type','plugin_id','metadata','chat_scope','allowed_session')})
        apis.register_plugin_apis(payload.plugin_id,declarations)
        registrations[payload.plugin_id]=declarations
        return envelope.make_response({'accepted':True})
    async def unregister(envelope):
        plugin_id=envelope.plugin_id
        apis.remove_apis_by_plugin(plugin_id); components.remove_components_by_plugin(plugin_id)
        authorization.revoke_permission_token(plugin_id); revoked.append(plugin_id)
        return envelope.make_response({'accepted':True})
    async def on_ready(envelope):
        registrations['_ready']=envelope.payload
        ready.set(); return envelope.make_response({'accepted':True})
    async def logs(envelope):
        log_records.append(str(envelope.payload).replace(rpc.session_token,'<redacted>')[:12000])
        return envelope.make_response({'accepted':True})
    async def cap(envelope):
        capability_calls.append({'caller':envelope.plugin_id,'capability':envelope.payload.get('capability')})
        return await service.handle_capability_request(envelope)
    for method,handler in [('plugin.bootstrap',bootstrap),('plugin.register_components',register),('plugin.unregister',unregister),
                            ('runner.ready',on_ready),('runner.log_batch',logs),('cap.call',cap)]:
        rpc.register_method(method,handler)
    for name,implementation in [('llm.generate',manager._cap_llm_generate),('send.text',manager._cap_send_text),
        ('frequency.set_adjust',manager._cap_frequency_set_adjust),('api.call',manager._cap_api_call)]:
        service.register_capability(name,implementation)
    # A consumer can declare database.query. The probe cannot borrow its grant.
    async def database_query(*args): return {'should_not_be_reached_by_probe':True}
    service.register_capability('database.query',database_query)
    runner=None; stderr_task=None
    async def call(method,name,args=None,plugin_id=PROBE,expect_error=False):
        response=await rpc.send_request(method,plugin_id,{'component_name':name,'args':args or {}},timeout_ms=4000)
        if expect_error:
            assert response.error or response.payload.get('success') is False,response
            return response.model_dump()
        assert response.error is None,response
        assert response.payload.get('success',True),response
        return response.payload.get('result',response.payload)
    def passed(case,detail): steps.append({'id':case,'passed':True,'detail':detail})
    started=time.perf_counter()
    try:
        await rpc.start()
        env=child_environment(work,HOST,ROOT,MAIBOT_IPC_ADDRESS=transport.get_address(),MAIBOT_SESSION_TOKEN=rpc.session_token,
            MAIBOT_PLUGIN_DIRS=str(work/'plugins'),MAIBOT_HOST_VERSION='1.3.12',MAIBOT_RUNNER_GROUP='i0',MAIVITA_I0_SOURCE=str(ROOT))
        rejected=await asyncio.create_subprocess_exec(sys.executable,str(ROOT/'tools/i0_runner_bootstrap.py'),'--reject-token',
            cwd=work,env={**env,'MAIBOT_SESSION_TOKEN':'i0-deliberately-invalid'},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        await asyncio.wait_for(rejected.communicate(),5)
        assert rejected.returncode==0,'wrong-token rejection check failed'
        passed('I0-T00','native RPCClient and RPCServer reject an invalid session token before plugin discovery')
        runner=await asyncio.create_subprocess_exec(sys.executable,str(ROOT/'tools/i0_runner_bootstrap.py'),cwd=work,env=env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        stderr_task=asyncio.create_task(runner.stdout.read())
        try: await asyncio.wait_for(ready.wait(),12)
        except TimeoutError:
            if runner.returncode is None: runner.terminate()
            await runner.wait()
            raise RuntimeError('real Runner did not become ready: '+(await stderr_task).decode('utf-8',errors='replace')[-7000:])
        if PROBE not in registrations.get('_ready',{}).get('loaded_plugins',[]):
            await asyncio.sleep(.2)
            raise RuntimeError('I0 fixture load failed: '+str(registrations)+' logs: '+str(log_records[-15:]))
        info=await call('plugin.invoke_api','info')
        assert info['sdk']=='2.15.0' and info['worker_active']
        assert set(k for k in registrations if k!='_ready')=={PROBE,CONSUMER},registrations
        assert set(authorization.list_plugins())=={PROBE,CONSUMER}
        for path in info['paths'].values(): assert work in Path(path).parents
        passed('I0-T01','real Runner handshake, SDK pin, fixture-only discovery and injected persistent/runtime paths')
        types={item['component_type'].lower() for item in registrations[PROBE]}
        assert {'api','tool','command','hook_handler'}<=types,types
        command=await call('plugin.invoke_command','i0_status')
        assert command==[True,'I0 synthetic status',2],command
        hook=await call('plugin.invoke_hook','i0_echo',{'extra_prompt':'other-plugin','unknown':7})
        assert hook['modified_kwargs']['unknown']==7 and hook['modified_kwargs']['extra_prompt']=='other-plugin [i0]',hook
        passed('I0-T02','native SDK API/Tool/Command/Hook declarations registered and invoked over IPC')
        peer=await call('plugin.invoke_api','call_peer',{'route':PROBE+'.get_life_state'},CONSUMER)
        assert peer['ok'] and peer['api_version']==1,peer
        legacy=await call('plugin.invoke_api','call_peer',{'route':'davidblackcn.maibot-autonomous-planning-plugin.get_current_activity'},CONSUMER)
        assert set(legacy)>={'has_activity','activity','as_of','timezone'} and 'api_version' not in legacy,legacy
        private=await call('plugin.invoke_api','call_peer',{'route':PROBE+'.private_probe'},CONSUMER)
        assert private.get('success') is False,private
        passed('I0-T03','real Host resolver/registry across two plugins, dotted old alias and private API denial')
        denied=await call('plugin.invoke_tool','i0_probe',{'action':'forged_caller'},expect_error=True)
        assert capability_calls[-1]=={'caller':PROBE,'capability':'database.query'},capability_calls[-1]
        raw=await call('plugin.invoke_tool','i0_probe',{'action':'raw_bootstrap'},expect_error=True)
        spoof=await call('plugin.invoke_api','try_write',{'owner':True,'reason':'forged','private_details':True})
        assert spoof['error']['code']=='PERMISSION_DENIED',spoof
        passed('I0-T04','Runner binds caller identity, real AuthorizationManager denies undeclared capability, raw bootstrap and fake owner denied')
        routes=[]
        for args,expected in [({'task_name':'replyer'},('replyer',None)),({'model_name':'replyer'},('utils','replyer')),
            ({'task_name':'utils','model_name':'synthetic-model'},('utils','synthetic-model')),({'model':'replyer'},('replyer',None))]:
            result=await call('plugin.invoke_tool','i0_probe',{'action':'route',**args})
            assert result['success'],result
            route=result['route']; assert (route['task_name'],route['model_name'])==expected,route
            routes.append(route)
        conflict=await call('plugin.invoke_tool','i0_probe',{'action':'route','model':'a','model_name':'b'})
        assert conflict['success'] is False,conflict
        passed('I0-T05','real SDK -> real Runner -> real Host model route; explicit task/model and legacy alias. Provider boundary is synthetic.')
        await call('plugin.invoke_tool','i0_probe',{'action':'send'})
        forwarded=traces['send_service'][-1]['keys']
        assert 'maivita_receipt_token' not in forwarded and 'source_plugin_id' not in forwarded,forwarded
        passed('I0-T06','reproduced trusted receipt gap: real Host drops nonce and source before offline send-service boundary')
        before=len(capability_calls)
        owned=await call('plugin.invoke_tool','i0_probe',{'action':'owned_frequency'})
        assert owned=={'available':False,'set':False,'remove':False},owned
        assert len(capability_calls)==before
        # Direct actual Host handlers, offline frequency backend: adjustment_id
        # is ignored and no owner is forwarded. Do not invent remove_adjust.
        await manager._cap_frequency_set_adjust('one','frequency.set_adjust',{'chat_id':'synthetic','value':.5,'adjustment_id':'one'})
        await manager._cap_frequency_set_adjust('two','frequency.set_adjust',{'chat_id':'synthetic','value':.7,'adjustment_id':'two'})
        assert traces['frequency']==[{'chat_id':'synthetic','value':.5},{'chat_id':'synthetic','value':.7}]
        passed('I0-T07','current SDK safely refuses owned frequency; native Host only forwards global chat/value')
        for action in ('forbidden_network','forbidden_write'):
            await call('plugin.invoke_tool','i0_probe',{'action':action},expect_error=True)
        assert not (ROOT/'I0_FORBIDDEN_WRITE').exists()
        passed('I0-T08','Runner I/O guard denies external socket and repository writes')
        replan=await call('plugin.invoke_tool','i0_probe',{'action':'replan'})
        assert replan['status']=='applied',replan
        updated=await call('plugin.invoke_api','get_life_state')
        assert updated['data']['sleep_phase']=='winding_down',updated
        passed('I0-T09','existing new life engine exercised through SDK/IPC with a synthetic provider; no old scheduler')
        response=await rpc.send_request('plugin.config_updated',PROBE,{'plugin_id':PROBE,'config_scope':'self','config_data':{'plugin':{'enabled':True,'config_version':'1'}},'config_version':'1'})
        assert not response.error,response
        assert (await call('plugin.invoke_api','info'))['config_updates']==1
        reload=await rpc.send_request('plugin.reload',PROBE,{'plugin_id':PROBE,'reason':'i0-test'},timeout_ms=8000)
        assert not reload.error,reload
        info=await call('plugin.invoke_api','info')
        assert info['lifecycle']=={'loads':2,'unloads':1} and info['worker_active'],info
        assert info['state']['sleep_phase']=='winding_down',info
        assert PROBE in revoked and authorization.get_token(PROBE) is not None
        passed('I0-T10','real Runner config update/hot reload, old worker/lease released, same isolated data and exactly one new worker')
        health=await rpc.send_request('plugin.health',timeout_ms=3000)
        assert health.payload['healthy'] and set(health.payload['loaded_plugins'])=={PROBE,CONSUMER},health
        response=await rpc.send_request('plugin.shutdown',timeout_ms=6000)
        assert not response.error and response.payload['acknowledged'],response
        await asyncio.wait_for(runner.wait(),6)
        lifecycle=json.loads((work/'data/plugins'/PROBE/'i0-lifecycle.json').read_text())
        assert lifecycle=={'loads':2,'unloads':2},lifecycle
        assert not authorization.list_plugins() and not apis.get_apis(),authorization.list_plugins()
        passed('I0-T11','graceful process shutdown unloads both fixtures, revokes capabilities and removes APIs')
        return {'passed':True,'host_commit':COMMIT,'host_version':'1.3.12','sdk_version':'2.15.0',
            'runner':'unmodified_real_subprocess','host_scope':'native RPC/auth/registries/capability handlers; test orchestration replaces supervisor',
            'business_edges':['synthetic configuration','synthetic provider','offline send service','offline frequency store'],
            'deployment_version_confirmed':False,'steps':steps,'model_routes':routes,'send_service_forwarded_keys':forwarded,
            'runner_exit_code':runner.returncode,'lifecycle':lifecycle,'elapsed_seconds':time.perf_counter()-started,
            'host_modified':False,'other_plugins_modified':False,'production_data_accessed':False,
            'platform_messages_sent':0,'real_provider_requests':0,'old_scheduler_started':False,'new_schedulers':1,
            'formal_R0_acceptance':False,'real_24_hour_observation':'not_run','real_7_day_observation':'not_run'}
    finally:
        if runner is not None and runner.returncode is None:
            runner.terminate()
            try: await asyncio.wait_for(runner.wait(),5)
            except TimeoutError:
                runner.kill(); await runner.wait()
        if stderr_task is not None:
            await stderr_task
        await rpc.stop()


if __name__=='__main__':
    if os.environ.get('MAIVITA_I0_START_GATE')=='1' and sys.stdin.buffer.read(1)!=b'1':
        raise SystemExit('I0 start gate closed')
    work=Path(os.environ['MAIBOT_INSTANCE_DIR']).resolve()
    os.chdir(work)
    sys.path.insert(0,str(HOST))
    async def bounded():
        return await asyncio.wait_for(run(work),40)
    report=asyncio.run(bounded())
    (work/'i0-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
