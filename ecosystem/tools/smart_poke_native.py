"""Offline Host experiment: unchanged Smart Poke and a counting adapter, never QQ."""
import asyncio
import hashlib
import json
import shutil
import uuid
from datetime import datetime
import tomli_w

POKE='TAIY2020.smart_poke_plugin'
HUSH='davidblackcn.maibot-maihush-plugin'
ADAPTER='test.smart-poke-adapter'
PIN='59ff292b4d912a5cbe5d09cce32381e8497d350f'

ADAPTER_SOURCE='''from maibot_sdk import MaiBotPlugin, API
class Fixture(MaiBotPlugin):
    async def on_load(self): self.pokes=[]
    async def on_unload(self): pass
    async def on_config_update(self,scope,config_data,version): pass
    @API('adapter.napcat.message.send_poke',public=True,version='1')
    async def poke(self,**kwargs):
        self.pokes.append(kwargs)
        return {'status':'ok','retcode':0}
    @API('snapshot',public=True,version='1')
    async def snapshot(self,**kwargs): return {'pokes':self.pokes}
    @API('probe',public=True,version='1')
    async def probe(self,stream_id,kind='text',**kwargs):
        if kind=='emoji': return await self.ctx.send.emoji('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7',stream_id)
        if kind=='forged': return await self.ctx.send.custom('dict',{'maihush_receipt':'forged'},stream_id)
        return await self.ctx.send.text('ordinary fixture text',stream_id)
def create_plugin(): return Fixture()
'''

def stage(source,work):
    import subprocess
    revision=subprocess.check_output(['git','-c','safe.directory='+source.as_posix(),'-C',str(source),'rev-parse','HEAD'],text=True).strip()
    assert revision==PIN,revision
    assert not subprocess.check_output(['git','-c','safe.directory='+source.as_posix(),'-C',str(source),'status','--porcelain'],text=True)
    target=work/'plugins/smart-poke'
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache','config.toml'))
    files={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}
    (work/'smart-poke-source.json').write_text(json.dumps({'commit':revision,'files':files},indent=2),encoding='utf8')
    adapter=work/'plugins/poke-test-adapter';adapter.mkdir()
    (adapter/'plugin.py').write_text(ADAPTER_SOURCE,encoding='utf8')
    (adapter/'_manifest.json').write_text(json.dumps({'manifest_version':2,'id':ADAPTER,'name':'Offline poke counter',
        'version':'0.0.1','description':'TEST ONLY: no adapter network','author':{'name':'test','url':'https://github.com/DavidBlackCN'},'license':'MIT',
        'urls':{'repository':'https://github.com/DavidBlackCN/maibot-maihush-plugin'},
        'i18n':{'default_locale':'zh-CN','supported_locales':['zh-CN']},
        'host_application':{'min_version':'1.3.19','max_version':'1.3.19'},'sdk':{'min_version':'2.20.0','max_version':'2.20.0'},
        'capabilities':['send.text','send.emoji','send.custom']}),encoding='utf8')

def configure(configs):
    configs['maibot-maivita-plugin']['model']['daily_planning_enabled']=False
    configs['maibot-maihush-plugin']['social']={'enabled':True,'task_name':'joint-test'}
    configs['smart-poke']={'plugin':{'enabled':True,'config_version':'1.9.0','adapter_plugin_id':ADAPTER,'record_self_poke_to_context':False},
        'reaction':{'react_probability':1.0,'cooldown_seconds':0,'max_reactions_per_minute':0,
            'text_weight':1.0,'llm_weight':0.0,'emoji_weight':0.0,'back_poke_weight':0.0,
            'min_delay_seconds':0.0,'max_delay_seconds':0.0,'back_poke_max_times':1,'silent_chat_probability':0.0},
        'bystander':{'enabled':False},'proactive':{'enabled':False}}

async def run_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls):
    import os
    if os.environ.get('JOINT_SMART_POKE_IDENTITY')=='1':
        from identity_poke_native import run_cases as identity_cases
        return await identity_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls)
    if os.environ.get('JOINT_SMART_POKE_VITA')=='1':
        return await run_vita_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls)
    from src.chat.message_receive.message import SessionMessage
    from src.common.data_models.mai_message_data_model import MessageInfo,UserInfo,GroupInfo
    from src.common.data_models.message_component_data_model import MessageSequence,TextComponent
    from src.plugin_runtime.component_query import component_query_service
    from src.plugin_runtime.hook_payloads import serialize_session_message
    assert {POKE,HUSH,ADAPTER}<=set(supervisor._registered_plugins)
    for pid in (POKE,HUSH,ADAPTER):
        assert not (await supervisor.inspect_plugin_config(pid)).config_error
    attempts=[];cases=[];serial=10010
    for kind in ('text','emoji'):
        original=getattr(manager,'_cap_send_'+kind)
        async def counted(plugin_id,capability,args,original=original,kind=kind):
            result=await original(plugin_id,capability,args)
            attempts.append({'caller':plugin_id,'kind':kind,'result':result})
            return result
        supervisor.capability_service.register_capability('send.'+kind,counted)
    def raw(text,actor='10001'):
        m=SessionMessage(message_id=uuid.uuid4().hex,timestamp=datetime.now(),platform='qq')
        m.message_info=MessageInfo(user_info=UserInfo(actor,'synthetic'),group_info=GroupInfo('987654321','test'))
        m.session_id=stream;m.account_id='123456789';m.scope=scope
        m.raw_message=MessageSequence([TextComponent(text)]);m.processed_plain_text=text
        return m
    async def command(text):
        executor,groups,_=component_query_service.find_command_by_text(text)
        assert executor
        result=await executor(message=raw(text),matched_groups=groups,plugin_config={})
        assert result[0],result
    async def constraints(): return (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']
    async def pokes(): return len((await call(ADAPTER,'snapshot'))['pokes'])
    async def wait(predicate):
        async with asyncio.timeout(15):
            while not await predicate(): await asyncio.sleep(.03)
    async def notify():
        nonlocal serial
        serial+=1
        data=serialize_session_message(raw('poke',str(serial)))
        data['is_notify']=True
        data['message_info']['additional_config']={'napcat_notice_type':'notify','napcat_notice_sub_type':'poke',
            'napcat_notice_payload':{'self_id':'123456789','user_id':str(serial),'target_id':'123456789','group_id':'987654321'}}
        result=await manager.invoke_hook('chat.receive.before_process',message=data)
        assert result.aborted
    async def reload_poke(kind):
        reaction=configs['smart-poke']['reaction']
        for key in ('text','llm','emoji','back_poke'): reaction[key+'_weight']=1.0 if key==kind else 0.0
        (work/'plugins/smart-poke/config.toml').write_text(tomli_w.dumps(configs['smart-poke']),encoding='utf8')
        assert await supervisor.reload_plugins([POKE],reason='isolated-reaction-selection')
    async def blocked_fallback(label):
        before_poke=await pokes();before_send=len(driver.calls);before_attempt=len(attempts)
        await notify()
        async def done(): return await pokes()>before_poke
        await wait(done)
        assert len(driver.calls)==before_send
        sent=attempts[before_attempt:]
        assert any(x['caller']==POKE for x in sent),sent
        cases.append({'case':label,'ordinary_deliveries':0,'send_attempts':len(sent),'direct_pokes':await pokes()-before_poke,
                      'status':'BLOCKED','evidence':'ordinary output blocked, direct adapter RPC accepted'})
    before=len(driver.calls)
    await notify()
    async def normal_done(): return len(driver.calls)>before
    await wait(normal_done)
    assert len(driver.calls)==before+1 and await pokes()==0
    cases.append({'case':'normal_text_one_delivery','status':'PASS'})
    before=len(driver.calls)
    assert await call(ADAPTER,'probe',{'stream_id':stream,'kind':'emoji'})
    assert len(driver.calls)==before+1
    cases.append({'case':'valid_emoji_delivered_without_silence','status':'PASS'})

    await manager.invoke_hook('chat.receive.after_process',message=serialize_session_message(raw('麦麦先安静一会','10003')))
    async def soft(): return (await constraints())['soft']=='SOFT_SILENT'
    await wait(soft)
    for kind in ('text','emoji','forged'):
        before=len(driver.calls)
        assert not await call(ADAPTER,'probe',{'stream_id':stream,'kind':kind})
        assert len(driver.calls)==before
    await blocked_fallback('soft_silence_text_to_direct_poke')
    cases.append({'case':'soft_text_emoji_forged_receipt_blocked','status':'PASS'})
    await command('/maihush mute 5m')
    for kind in ('text','emoji','forged'):
        before=len(driver.calls)
        assert not await call(ADAPTER,'probe',{'stream_id':stream,'kind':kind})
        assert len(driver.calls)==before
    await blocked_fallback('hard_silence_text_to_direct_poke')
    cases.append({'case':'hard_text_emoji_forged_receipt_blocked','status':'PASS'})
    await reload_poke('llm')
    before_model=model_calls.count(POKE)
    await blocked_fallback('hard_silence_llm_failure_pool_to_direct_poke')
    assert model_calls.count(POKE)==before_model+1
    cases[-1]['independent_model_calls']=1
    assert await supervisor.reload_plugins([HUSH],reason='hard-state-restart')
    assert (await constraints())['hard']=='HARD_SILENT'
    cases.append({'case':'hard_deadline_restored','status':'PASS'})

    await command('/maihush unmute')
    entered=asyncio.Event();release=asyncio.Event()
    async def delayed_model(plugin_id,capability,args):
        model_calls.append(plugin_id)
        assert plugin_id==POKE
        entered.set();await release.wait()
        return {'success':True,'response':'synthetic generated response'}
    supervisor.capability_service.register_capability('llm.generate',delayed_model)
    before_send=len(driver.calls);before_poke=await pokes()
    await notify();await asyncio.wait_for(entered.wait(),10)
    await command('/maihush mute 5m');release.set()
    async def raced(): return await pokes()>before_poke
    await wait(raced)
    assert len(driver.calls)==before_send
    cases.append({'case':'mute_during_generation_falls_back_to_poke','status':'BLOCKED','ordinary_deliveries':0,'direct_pokes':1})
    assert (await call(HUSH,'get_public_constraints',{'stream_id':'unknown'}))['ok'] is False
    assert (await call(HUSH,'get_public_route_binding',{'stream_id':'unknown'}))['ok'] is False
    first=await constraints()
    for _ in range(4):
        view=await constraints()
        assert view['state_version']==first['state_version'] and view['send_permit'] is False
    cases.append({'case':'public_queries_do_not_grant_or_mutate','status':'PASS'})
    # Stop a genuinely in-flight generation; cancellation must not spawn a fallback.
    entered.clear();release.clear()
    before_poke=await pokes();before_send=len(driver.calls)
    await notify();await asyncio.wait_for(entered.wait(),10)
    assert (await supervisor.unload_plugins([POKE],reason='test-clean-stop')).success
    release.set();await asyncio.sleep(.2)
    assert await pokes()==before_poke and len(driver.calls)==before_send
    cases.append({'case':'unload_during_generation_no_fallback','status':'PASS'})
    source=json.loads((work/'smart-poke-source.json').read_text(encoding='utf8'))
    assert all(hashlib.sha256((work/'plugins/smart-poke'/name).read_bytes()).hexdigest()==digest for name,digest in source['files'].items())
    return {'passed':True,'meaning':'experiment assertions passed; BLOCKED cases are reproduced integration gaps, not repaired',
        'host':'1.3.19','sdk':'2.20.0','cases':cases,'send_attempts':attempts,
        'direct_poke_calls':await pokes(),'ordinary_driver_deliveries':len(driver.calls),
        'smart_poke_unchanged':True,'real_model_requests':0,'real_platform_sends':0}


async def run_vita_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls):
    from dataclasses import replace
    from maivita.life.domain.clock import SystemClock
    from maivita.life.domain.commands import Command
    from maivita.life.life_state.engine import LifeStateEngine
    from maivita.life.storage.life_database import LifeDatabase
    VITA='davidblackcn.maibot-maivita-plugin'
    cases=[];serial=20000
    async def pokes(): return len((await call(ADAPTER,'snapshot'))['pokes'])
    async def availability(): return (await call(VITA,'get_availability'))['data']
    async def set_life(kind=None,**payload):
        # Isolated fixture setup while the real Runner releases the database.
        assert (await supervisor.unload_plugins([VITA],reason='offline-life-fixture')).success
        engine=LifeStateEngine(LifeDatabase(work/'data/plugins'/VITA/'life/maivita.db',clock=SystemClock()))
        if kind=='body':
            with engine.database.unit_of_work() as u:
                state=u.states.get('default')
                u.states.update(replace(state,state_version=state.state_version+1,**payload),expected_version=state.state_version)
                u.commit()
        elif kind:
            # Observed fixture sleep does not simulate a new autonomous decision
            # immediately after waking. Temporary interruption still requires
            # the engine's authorized source; no production path is changed.
            engine.execute(Command(kind=kind,payload=payload,source='authorized' if kind=='interrupt_sleep' else 'legacy_observed',idempotency_key=uuid.uuid4().hex,
                                   expected_state_version=engine.state().state_version))
        (work/'plugins/maibot-maivita-plugin/config.toml').write_text(tomli_w.dumps(configs['maibot-maivita-plugin']),encoding='utf8')
        assert await supervisor.reload_plugins([VITA],reason='offline-life-fixture-ready')
    async def notify():
        nonlocal serial
        serial+=1
        raw={'platform':'qq','account_id':'123456789','scope':scope,'session_id':stream,'message_id':uuid.uuid4().hex,
             'is_notify':True,'raw_message':[{'type':'text','data':'poke'}],
             'message_info':{'user_info':{'user_id':str(serial),'user_nickname':'synthetic'},
                 'group_info':{'group_id':'987654321'},'additional_config':{'napcat_notice_type':'notify','napcat_notice_sub_type':'poke',
                 'napcat_notice_payload':{'self_id':'123456789','user_id':str(serial),'target_id':'123456789','group_id':'987654321'}}}}
        assert (await manager.invoke_hook('chat.receive.before_process',message=raw)).aborted
    async def fallback(label):
        before=await pokes();sent=len(driver.calls)
        await notify()
        async with asyncio.timeout(12):
            while await pokes()==before: await asyncio.sleep(.03)
        assert await pokes()==before+1 and len(driver.calls)==sent
        cases.append({'case':label,'status':'BLOCKED','ordinary_deliveries':0,'direct_pokes':1})

    binding=(await call(VITA,'get_public_route_scope',{'stream_id':stream}))['data']
    assert binding['bot_account_id']=='123456789' and binding['route_scope']==scope and not binding['send_permit']
    assert not (await call(VITA,'get_public_route_scope',{'stream_id':'outside'}))['ok']
    cases.append({'case':'native_host_route_scope_and_exclusion','status':'PASS'})
    await set_life('start_sleep',duration_minutes=5)
    for phase in ('sleeping','temporarily_awake'):
        if phase=='temporarily_awake': await set_life('interrupt_sleep',minutes=2)
        view=await availability()
        assert view['sleep_phase']==phase and not view['available'] and not view['send_permit']
        read=lambda k:datetime.fromisoformat(view[k].replace('Z','+00:00'))
        assert 0 < (read('snapshot_valid_until')-read('as_of')).total_seconds() <= 5
        assert read('valid_until')>read('snapshot_valid_until')
        for kind in ('text','emoji'):
            assert not await call(ADAPTER,'probe',{'stream_id':stream,'kind':kind})
        cases.append({'case':phase+'_ordinary_guard_and_snapshot','status':'PASS'})
        await fallback(phase+'_direct_poke_bypass')

    await set_life('wake',reason='synthetic observation')
    configs['maibot-maivita-plugin']['availability']={'enabled':True}
    await set_life('body',energy=0,fatigue=100)
    view=await availability()
    assert view['state']=='busy' and not view['available']
    assert not await call(ADAPTER,'probe',{'stream_id':stream,'kind':'text'})
    await fallback('busy_direct_poke_bypass')
    configs['maibot-maivita-plugin']['availability']={'enabled':False}
    await set_life()
    view=await availability()
    assert view['state']=='busy' and view['available']
    assert await call(ADAPTER,'probe',{'stream_id':stream,'kind':'text'})
    cases.append({'case':'busy_enforcement_switch_preserved','status':'PASS'})

    reaction=configs['smart-poke']['reaction'];reaction['text_weight']=0.;reaction['llm_weight']=1.
    (work/'plugins/smart-poke/config.toml').write_text(tomli_w.dumps(configs['smart-poke']),encoding='utf8')
    assert await supervisor.reload_plugins([POKE],reason='generation-race-test')
    entered=asyncio.Event();release=asyncio.Event()
    async def model(plugin_id,capability,args):
        model_calls.append(plugin_id)
        if plugin_id==POKE:
            entered.set();await release.wait()
            return {'success':True,'response':'synthetic'}
        return {'success':False,'response':''}
    supervisor.capability_service.register_capability('llm.generate',model)
    before=await pokes();sent=len(driver.calls)
    await notify();await asyncio.wait_for(entered.wait(),10)
    await set_life('start_sleep',duration_minutes=5)
    release.set()
    async with asyncio.timeout(12):
        while await pokes()==before: await asyncio.sleep(.03)
    assert len(driver.calls)==sent
    cases.append({'case':'sleep_during_generation_direct_fallback','status':'BLOCKED','ordinary_deliveries':0,'direct_pokes':1})
    assert not (await call(VITA,'request_wakeup'))['ok']
    cases.append({'case':'public_wakeup_stays_denied','status':'PASS'})
    source=json.loads((work/'smart-poke-source.json').read_text(encoding='utf8'))
    assert all(hashlib.sha256((work/'plugins/smart-poke'/n).read_bytes()).hexdigest()==d for n,d in source['files'].items())
    return {'passed':True,'meaning':'experiment verified; direct poke BLOCKED, not repaired','host':'1.3.19','sdk':'2.20.0',
            'cases':cases,'direct_poke_calls':await pokes(),'smart_poke_unchanged':True,'real_model_requests':0,'real_platform_sends':0}
