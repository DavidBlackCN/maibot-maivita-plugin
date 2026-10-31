"""Real Supervisor/SDK/three domain plugins/coordinator, synthetic model and driver."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import asyncio
import json
import os
import sys
import time
import uuid
import importlib.metadata
import tomli_w

VITA='davidblackcn.maibot-maivita-plugin'
HUSH='davidblackcn.maibot-maihush-plugin'
IDENTITY='davidblackcn.maibot-maiidentity-plugin'
COORD='davidblackcn.maibot-maicoordinate-plugin'


async def run(work):
    hush_experience=os.environ.get('JOINT_HUSH_EXPERIENCE')=='1'
    automatic=os.environ.get('JOINT_AUTO_ROUTES')=='1'
    space=os.environ.get('JOINT_SPACE')=='1'
    trusted=os.environ.get('JOINT_TRUSTED_MANAGEMENT')=='1'
    vita_context=os.environ.get('JOINT_VITA_CONTEXT')=='1'
    scope=None if automatic else 'synthetic-test-scope'
    from src.config.config import config_manager
    config_manager.initialize(); cfg=config_manager.get_global_config()
    # SnowLuma supplies account_id per route; the official fallback may be empty.
    cfg.bot.qq_account=''; cfg.bot.platform=''; cfg.telemetry.enable=False; cfg.maim_message.enabled=False
    from src.common.database.database import initialize_database,engine
    initialize_database()
    from src.chat.message_receive.chat_manager import chat_manager
    session=await chat_manager.get_or_create_session('qq','10001','987654321',account_id='123456789',scope=scope)
    stream=session.session_id
    configs={
        'maibot-maihush-plugin':{'plugin':{'enabled':True,'config_version':'2'},'groups':[{'group_id':'987654321','route_scope':scope or ''}], 'management':{'source':'local','admin_accounts':['10001','10002']},'reply':{'enabled':False}},
        'maibot-maiIdentity-plugin':{'plugin':{'enabled':True,'config_version':'4'},
            'identity':{'adapter_namespace':'synthetic-test-adapter','route_scope':'synthetic-test-scope','people':[{'qq':'10002','nickname':'妈妈','prompt_template':'private fixed profile'}]}},
        'maibot-maivita-plugin':{'plugin':{'enabled':True,'config_version':'4'},
            'instance':{'writer_lock':str(work/'vita.lock'),'allowed_streams':[stream]},'model':{'generate_on_start':False}},
        'maibot-maicoordinate-plugin':{'plugin':{'enabled':True,'config_version':'1'},'mode':'shadow','acknowledgement':'isolated-test-instance','model_task':'joint-test','poll_seconds':1,
            'routes':[{'route_id':'test','instance_id':'joint-test','adapter_namespace':'host-qq','bot_account_id':'123456789','route_scope':'synthetic-test-scope','group_id':'987654321','stream_id':stream}]}}
    if automatic:
        configs['maibot-maicoordinate-plugin']['routes']=[]
        configs['maibot-maiIdentity-plugin']['identity']['route_scope']=''
        configs['maibot-maihush-plugin']['groups'][0]['route_scope']=''
    route_id=stream if automatic else 'test'
    if trusted: configs['maibot-maiIdentity-plugin']['management']={'admin_accounts':['10001']}
    if vita_context:
        configs['maibot-maivita-plugin']['model']['daily_planning_enabled']=False
    if space:
        configs['maibot-maicoordinate-plugin']['routes']=[]
        configs['maibot-maicoordinate-plugin']['plugin']['config_version']='2'
        configs['maibot-maicoordinate-plugin']['space']={'mode':'controlled','interaction_whitelist':[]}
        configs['MaiTrace']={'plugin':{'config_version':'6.3.0'},'life':{'enabled':False},'schedule':{'enabled':False},'messaging':{'enabled':False},
            'routine':{'post_enabled':False,'browse_enabled':False},'send':{'enable_chat_memory':False},
            'image':{'enable_image':False},'monitor':{'read_list_type':'whitelist','read_list':['10001'],
                'enable_auto_reply':True,'like_probability':1.0,'comment_probability':1.0,'silent_hours':''}}
    if hush_experience:
        cfg.bot.nickname='小桃'
        configs['maibot-maiIdentity-plugin']['management']={'admin_accounts':['10001']}
        configs['maibot-maihush-plugin']['management']={'source':'identity'}
        configs['maibot-maihush-plugin']['reply']={'enabled':True}
    for folder,config in configs.items():
        (work/'plugins'/folder/'config.toml').write_text(tomli_w.dumps(config),encoding='utf8')
    # Seed a real life database while no Runner owns it; startup will perform
    # actual deadline recovery. This is synthetic life input, not a fake API.
    sys.path.insert(0,str(work/'plugins/maibot-maivita-plugin'))
    from maivita.life.domain.clock import ManualClock,SystemClock
    from maivita.life.domain.commands import Command
    from maivita.life.life_state.engine import LifeStateEngine
    from maivita.life.storage.life_database import LifeDatabase
    vita_path=work/'data/plugins'/VITA/'life/maivita.db'
    vita_path.parent.mkdir(parents=True)
    clock=ManualClock(datetime.now(timezone.utc)-timedelta(minutes=21 if space else 6))
    db=LifeDatabase.create(vita_path,clock=clock); life=LifeStateEngine(db); life.initialize()
    if space:
        life.execute(Command(kind='start_activity',payload={'name':'安静阅读','category':'rest','visibility':'public_summary'},source='legacy_observed',idempotency_key='space-fixture',expected_state_version=0))
        clock.advance(timedelta(minutes=21))
        life.execute(Command(kind='advance',payload={'initialize_neutral':True},idempotency_key='space-observed-until-now',expected_state_version=1))
    elif not hush_experience:
        life.execute(Command(kind='start_sleep',payload={'duration_minutes':5},source='legacy_observed',idempotency_key='sleep-fixture',expected_state_version=0))
    from src.plugin_runtime.integration import get_plugin_runtime_manager
    from src.plugin_runtime.host.supervisor import PluginSupervisor
    manager=get_plugin_runtime_manager(); manager.ensure_builtin_hook_specs_registered()
    supervisor=PluginSupervisor(plugin_dirs=[work/'plugins'],group_name='joint-test',hook_spec_registry=manager._hook_spec_registry,
        socket_path=os.environ.get('MAIHUSH_TEST_SOCKET_PATH'),max_restart_attempts=0,health_check_interval_sec=300,runner_spawn_timeout_sec=25)
    manager._third_party_supervisor=supervisor; manager._register_capability_impls(supervisor)
    model_calls=[]
    async def models(plugin_id,capability,args): return ['joint-test']
    async def model(plugin_id,capability,args):
        model_calls.append(plugin_id)
        if plugin_id==HUSH:
            data=json.loads(args['prompt'][1]['content'])
            return {'success':True,'response':json.dumps({'schema_version':1,'intent':'pause','target':'bot','decision':'soft_silence','duration_seconds':60,'reason_code':'RESPECT_REQUEST','evidence_message_ids':[data['request']['id']],'reply':'好，你们先聊。'})}
        if plugin_id==COORD: return {'success':True,'response':'隔离测试：我醒啦。','model_name':'synthetic'}
        if plugin_id=='Rabbit-Jia-Er.MaiTrace': return {'success':True,'response':'隔离测试：安静地读了一会儿书。','model_name':'synthetic'}
        return {'success':False,'response':'','model_name':'synthetic-unavailable'}
    supervisor.capability_service.register_capability('llm.get_available_models',models)
    supervisor.capability_service.register_capability('llm.generate',model)
    from src.platform_io.manager import get_platform_io_manager
    from src.platform_io.drivers.base import PlatformIODriver
    from src.platform_io.types import DriverDescriptor,DriverKind,DeliveryStatus,DeliveryReceipt,RouteBinding
    class CountedDriver(PlatformIODriver):
        def __init__(self):
            super().__init__(DriverDescriptor('joint-counted',DriverKind.LOCAL,'qq','123456789',scope)); self.calls=[];self.texts=[]
        async def send_message(self,message,route_key,metadata=None):
            self.calls.append(message.session_id)
            from src.common.data_models.message_component_data_model import TextComponent
            self.texts.append(''.join(c.text for c in message.raw_message.components if isinstance(c,TextComponent)))
            return DeliveryReceipt(message.message_id,route_key,DeliveryStatus.SENT,driver_id=self.driver_id,driver_kind=DriverKind.LOCAL,external_message_id='test-'+message.message_id)
    port=get_platform_io_manager(); driver=CountedDriver(); await port.add_driver(driver)
    port.send_route_table.bind(RouteBinding(driver.descriptor.route_key,driver.driver_id,DriverKind.LOCAL,priority=1000))
    async def call(plugin,name,args=None):
        reply=await supervisor.invoke_api(plugin,name,args=args or {},timeout_ms=8000)
        assert reply.error is None and reply.payload.get('success'),reply
        return reply.payload['result']
    async def wait_for(predicate,seconds=20):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if await predicate(): return
            await asyncio.sleep(.2)
        raise AssertionError('condition timed out: '+str(await call(COORD,'get_status'))+' snapshot='+str(await call(COORD,'get_snapshot',{'route_id':route_id})))
    async def delivered(): return len(driver.calls)==1
    async def observed(): return (await call(COORD,'get_status'))['data']['counts'].get('shadow',0)>=1
    async def new_wakeup():
        assert (await supervisor.unload_plugins([VITA],reason='offline-fixture')).success
        current=LifeStateEngine(LifeDatabase(vita_path,clock=SystemClock()))
        for kind,payload in [('start_sleep',{'duration_minutes':5}),('wake',{'reason':'synthetic observation'})]:
            current.execute(Command(kind=kind,payload=payload,source='legacy_observed',
                idempotency_key=uuid.uuid4().hex,expected_state_version=current.state().state_version))
        assert await supervisor.reload_plugins([VITA],reason='offline-fixture-complete')
    cases=[]
    try:
        await supervisor.start()
        if hush_experience:
            from hush_native_experience import run_cases
            return await run_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls)
        if vita_context:
            from vita_context_native import context_cases
            return await context_cases(work,supervisor,manager,call,configs,stream,vita_path,model_calls,driver)
        if space:
            from phase1_native_space import space_cases
            return await space_cases(work,supervisor,call,configs,model_calls,vita_path)
        assert {VITA,HUSH,IDENTITY,COORD}<=set(supervisor._registered_plugins),supervisor._registered_plugins
        cases.append('four_real_plugins_loaded_on_pinned_host_sdk')
        # The actual Runner endpoint used by WebUI must return a renderable
        # schema, not just accept config_model defaults during activation.
        for plugin_id in (VITA, IDENTITY, HUSH, COORD):
            inspected=await supervisor.inspect_plugin_config(plugin_id)
            assert not inspected.config_error,(plugin_id,inspected.config_error)
            sections=inspected.config_schema.get('sections')
            assert sections,plugin_id
            for section in sections.values():
                for field in section['fields'].values():
                    if field['ui_type']=='select': assert '' not in (field.get('choices') or []),(plugin_id,field)
        broken=await supervisor.inspect_plugin_config(IDENTITY,{'plugin':{'enabled':True}},use_provided_config=True)
        assert 'config_version' in broken.config_error
        repaired=await supervisor.inspect_plugin_config(IDENTITY,{'plugin':{'enabled':True,'config_version':'4'}},use_provided_config=True)
        assert not repaired.config_error and repaired.config_schema['sections']
        cases.append('WebUI_native_config_schema_and_missing_version_recovery')
        if automatic:
            from src.chat.message_receive.message import SessionMessage
            from src.common.data_models.mai_message_data_model import MessageInfo,UserInfo,GroupInfo
            from src.common.data_models.message_component_data_model import MessageSequence,TextComponent
            observed_message=SessionMessage(message_id=uuid.uuid4().hex,timestamp=datetime.now(),platform='qq')
            observed_message.message_info=MessageInfo(user_info=UserInfo('10001','synthetic'),group_info=GroupInfo('987654321','synthetic'))
            observed_message.session_id=stream;observed_message.account_id='123456789';observed_message.scope=None
            observed_message.raw_message=MessageSequence([TextComponent('synthetic route observation')]);observed_message.processed_plain_text='synthetic route observation'
            from src.plugin_runtime.hook_payloads import serialize_session_message
            await manager.invoke_hook('chat.receive.after_process',message=serialize_session_message(observed_message))
            async def bound(): return (await call(COORD,'get_snapshot',{'route_id':route_id}))['ok']
            await wait_for(bound)
            await asyncio.sleep(1.2)
            assert not driver.calls and COORD not in model_calls
            cases.append('null_scope_autodiscovery_matches_hush_and_ignores_historical_wakeup')
            await new_wakeup()
        await wait_for(observed)
        assert not driver.calls and COORD not in model_calls
        cases.append('native_shadow_consumes_real_wakeup_without_model_or_send')
        configs['maibot-maicoordinate-plugin']['mode']='controlled'
        (work/'plugins/maibot-maicoordinate-plugin/config.toml').write_text(
            tomli_w.dumps(configs['maibot-maicoordinate-plugin']),encoding='utf8')
        assert await supervisor.reload_plugins([COORD],reason='explicit-controlled-config')
        await asyncio.sleep(1)
        assert not driver.calls
        cases.append('shadow_to_controlled_does_not_replay_shadow_event')
        await new_wakeup()
        await wait_for(delivered)
        await asyncio.sleep(1.5)
        status=await call(COORD,'get_status')
        assert status['data']['counts'].get('sent_some')==1,status
        assert driver.calls==[stream]
        cases.append('real_wakeup_public_pull_durable_decision_generation_SendService_counted_driver')
        assert (await call(IDENTITY,'is_admin',{'owner':True,'proof_ref':'fake'}))=={'is_admin':False}
        assert not (await call(VITA,'request_wakeup',{'owner':True}))['ok']
        assert not (await call(HUSH,'get_state',{'owner':True}))['ok']
        cases.append('forged_management_context_stays_denied')
        assert await supervisor.reload_plugins([COORD],reason='verify-dedup')
        await asyncio.sleep(2)
        assert len(driver.calls)==1
        cases.append('coordinator_restart_does_not_resend_event')
        from src.chat.message_receive.message import SessionMessage
        from src.common.data_models.mai_message_data_model import MessageInfo,UserInfo,GroupInfo
        from src.common.data_models.message_component_data_model import MessageSequence,TextComponent
        from src.plugin_runtime.component_query import component_query_service
        m=SessionMessage(message_id=uuid.uuid4().hex,timestamp=datetime.now(),platform='qq')
        m.message_info=MessageInfo(user_info=UserInfo('10001','synthetic'),group_info=GroupInfo('987654321','synthetic'))
        m.session_id=stream; m.account_id='123456789'; m.scope=scope
        m.raw_message=MessageSequence([TextComponent('/maihush mute 20m')]); m.processed_plain_text='/maihush mute 20m'
        executor,groups,_=component_query_service.find_command_by_text('/maihush mute 20m')
        assert (await executor(message=m,matched_groups=groups,plugin_config={}))[0]
        snapshot=await call(COORD,'get_snapshot',{'route_id':route_id,'owner':True})
        assert snapshot['data']['decision']=='SPEECH_CONSTRAINED',snapshot
        assert len(driver.calls)==1
        cases.append('real_Hush_command_blocks_aggregate_without_waking_or_bypass')
        # Offline fixture inserts a second real sleep/wake cycle using the
        # engine; no plugin writes somebody else's DB while it is running.
        await new_wakeup()
        async def cancelled(): return (await call(COORD,'get_status'))['data']['counts'].get('cancelled',0)>=1
        await wait_for(cancelled)
        assert len(driver.calls)==1
        cases.append('new_public_wakeup_under_hard_silence_is_consumed_without_send')
        assert (await supervisor.unload_plugins([IDENTITY],reason='dependency-failure-test')).success
        snapshot=await call(COORD,'get_snapshot',{'route_id':route_id})
        assert snapshot['data']['snapshot']['error'] and snapshot['data']['decision']!='ELIGIBLE',snapshot
        assert len(driver.calls)==1
        assert await supervisor.reload_plugins([IDENTITY],reason='dependency-restored')
        cases.append('missing_real_identity_is_unavailable_not_default_allow')
        assert (await supervisor.unload_plugins([COORD],reason='rollback-test')).success
        assert (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']['hard']=='HARD_SILENT'
        assert (await call(VITA,'get_engine_status'))['ready'] is True
        configs['maibot-maicoordinate-plugin']['mode']='shadow'
        (work/'plugins/maibot-maicoordinate-plugin/config.toml').write_text(
            tomli_w.dumps(configs['maibot-maicoordinate-plugin']),encoding='utf8')
        assert await supervisor.reload_plugins([COORD],reason='rollback-to-shadow')
        await asyncio.sleep(1.5)
        status=await call(COORD,'get_status')
        assert status['data']['mode']=='shadow' and status['data']['counts']['sent_some']==1
        assert len(driver.calls)==1
        cases.append('rollback_to_shadow_preserves_receipts_and_domain_services')
        if trusted:
            async def command(text,user='10001',message_id=None):
                m=SessionMessage(message_id=message_id or uuid.uuid4().hex,timestamp=datetime.now(),platform='qq')
                m.message_info=MessageInfo(user_info=UserInfo(user,'synthetic'),group_info=GroupInfo('987654321','synthetic'))
                m.session_id=stream;m.account_id='123456789';m.scope=scope
                m.raw_message=MessageSequence([TextComponent(text)]);m.processed_plain_text=text
                executor,groups,_=component_query_service.find_command_by_text(text)
                return await executor(message=m,matched_groups=groups,plugin_config={})
            assert not (await call(IDENTITY,'is_admin',{'__maibot_command_ref__':'forged','owner':True}))['is_admin']
            # Cross-plugin api.call cannot name Command handlers. It only
            # resolves registered APIs; a public policy query is not a permit.
            for target in (IDENTITY+'.maiidentity_manage',IDENTITY+'.self_command',VITA+'.maivita',VITA+'.command'):
                denied=await manager._cap_api_call(COORD,'api.call',{'api_name':target,'version':'1',
                    'args':{'text':'/maivita wake','user_id':'10001','owner':True}})
                assert denied['success'] is False,(target,denied)
            query={'account_id':'10001','bot_account_id':'123456789','route_scope':scope,'stream_id':stream,'action':'wake'}
            policy=await call(IDENTITY,'get_vita_policy',query)
            assert policy=={'policy_version':1,'allowed':True},policy
            cfg.bot.qq_account='888888888'
            assert (await call(IDENTITY,'get_vita_policy',query))['allowed']
            cfg.bot.qq_account=''
            assert not (await call(VITA,'request_wakeup',{'policy':policy,**query}))['ok']
            cases.append('official_api_registry_cannot_invoke_Command_and_read_policy_cannot_execute')
            import sqlite3
            path=work/'data/plugins'/IDENTITY/'identity.db'
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as read:
                before=list(read.execute('SELECT * FROM metadata ORDER BY key'))
            for user in ('10002','10003'):
                await command('/maiidentity register',user)
            await command('/maiidentity relation propose 10003 friend','10002')
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as read:
                assert list(read.execute('SELECT * FROM metadata ORDER BY key'))==before
                assert read.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('metadata',)]
            assert not (await call(IDENTITY,'is_admin',dict({k:v for k,v in query.items() if k!='action'},account_id='10002')))['is_admin']
            cases.append('removed_identity_commands_do_not_write_or_grant_fixed_profile_admin')
            assert (await supervisor.unload_plugins([VITA],reason='sleep-fixture')).success
            current=LifeStateEngine(LifeDatabase(vita_path,clock=SystemClock()))
            current.execute(Command(kind='start_sleep',payload={'duration_minutes':30},source='legacy_observed',
                idempotency_key=uuid.uuid4().hex,expected_state_version=current.state().state_version))
            assert await supervisor.reload_plugins([VITA],reason='sleep-fixture-ready')
            await command('/maivita wake','10002')
            assert (await call(VITA,'get_life_state'))['data']['sleep_phase']=='sleeping'
            identifier=uuid.uuid4().hex
            await command('/maivita 醒来',message_id=identifier)
            assert (await call(VITA,'get_life_state'))['data']['sleep_phase']=='awake'
            assert await supervisor.reload_plugins([VITA],reason='command-replay-after-reload')
            version=(await call(VITA,'get_life_state'))['state_version']
            await command('/maivita 醒来',message_id=identifier)
            assert (await call(VITA,'get_life_state'))['state_version']==version
            assert (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']['hard']=='HARD_SILENT'
            assert len(driver.calls)==1
            await command('/maivita 重新规划')
            cases.append('official_Vita_Command_read_only_policy_wakeup_replay_after_reload_hard_silence_independence')
            configs['maibot-maiIdentity-plugin']['management']['admin_accounts']=[]
            (work/'plugins/maibot-maiIdentity-plugin/config.toml').write_text(
                tomli_w.dumps(configs['maibot-maiIdentity-plugin']),encoding='utf8')
            assert await supervisor.reload_plugins([IDENTITY],reason='remove-admin')
            assert not (await call(IDENTITY,'get_vita_policy',query))['allowed']
            before=(await call(VITA,'get_life_state'))['state_version']
            await command('/maivita wake')
            assert (await call(VITA,'get_life_state'))['state_version']==before
            assert len(driver.calls)==1
            cases.append('official_policy_admin_removal_reload_denies_management')
            cases.append('adapter_reported_identity_with_empty_global_fallback')
            # Product features through the actual official Command and Hook
            # registries. Inspect only this synthetic instance's owned data.
            configs['maibot-maiIdentity-plugin']['management']['admin_accounts']=['10001']
            (work/'plugins/maibot-maiIdentity-plugin/config.toml').write_text(tomli_w.dumps(configs['maibot-maiIdentity-plugin']),encoding='utf8')
            assert await supervisor.reload_plugins([IDENTITY],reason='restore-configured-admin')
            social=await call(IDENTITY,'get_public_social_context',{'stream_id':stream,'bot_account_id':'123456789','route_scope':scope})
            assert social['ok'] and not any(social['data']['counts'].values()),social
            assert '10002' not in json.dumps(social) and 'private fixed profile' not in json.dumps(social)
            persona=await call(VITA,'get_public_persona')
            assert persona['ok'] and persona['data']['source']=='official_config',persona
            cases.append('shared_official_persona_and_neutral_group_context_no_private_profile_leak')
            from src.plugin_runtime.hook_payloads import serialize_prompt_items,deserialize_prompt_items
            from src.llm_models.payload_content.context_item import UserMessageItem,ContextItemMeta,ContextTextPart
            from src.llm_models.payload_content.context_protocol import ContextProtocolMode
            original=[UserMessageItem(meta=ContextItemMeta.create(),parts=(ContextTextPart(text='synthetic user text'),))]
            payload=serialize_prompt_items(original)
            hooked=await manager.invoke_hook('maisaka.planner.before_request',items=payload,item_schema_version=1,tool_definitions=[],session_id=stream)
            assert not hooked.errors and not hooked.aborted,hooked
            assert hooked.kwargs['items'][0]==payload[0] and len(hooked.kwargs['items'])==3,hooked.kwargs
            restored=deserialize_prompt_items(hooked.kwargs['items'],item_schema_version=1,mode=ContextProtocolMode.REQUEST_CONTEXT,original_items=original)
            assert len(restored)==3 and restored[0]==original[0]
            cases.append('official_typed_planner_roundtrip_preserves_original_items_and_appends_both_public_contexts')
            journal=vita_path.with_name('management.sqlite3')
            async def management(text,user='10001'):
                await command(text,user)
                with sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True) as read:
                    row=read.execute('SELECT token,status FROM requests ORDER BY rowid DESC LIMIT 1').fetchone()
                assert row and row[1]=='pending',(text,row)
                await command('/maivita confirm '+row[0],user)
                with sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True) as read:
                    state,result=read.execute('SELECT status,result FROM requests WHERE token=?',(row[0],)).fetchone()
                assert state=='applied',(text,state,result)
                return row[0],json.loads(result)
            await command('麦麦管理 新增目标 联合验收目标')
            with sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True) as read:
                token=read.execute('SELECT token FROM requests ORDER BY rowid DESC LIMIT 1').fetchone()[0]
            await command('/maivita confirm '+token,'10002')
            with sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True) as read:
                assert read.execute('SELECT status FROM requests WHERE token=?',(token,)).fetchone()[0]=='pending'
            await command('/maivita confirm '+token)
            with sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True) as read:
                status,receipt=read.execute('SELECT status,result FROM requests WHERE token=?',(token,)).fetchone()
            assert status=='applied',(status,receipt)
            goal=json.loads(receipt)['target_id']
            assert await supervisor.reload_plugins([VITA],reason='product-journal-recovery')
            before=(await call(VITA,'get_life_state'))['state_version']
            await command('/maivita confirm '+token)
            assert (await call(VITA,'get_life_state'))['state_version']==before
            cases.append('native_natural_goal_preview_same_actor_confirmation_and_reload_idempotency')
            _,receipt=await management('/maivita activity start study 联合验收阅读')
            activity=receipt['primary_activity_id']
            await asyncio.sleep(.02)
            await management('麦麦管理 结束当前活动')
            await management('/maivita goal complete '+goal+' '+activity)
            _,receipt=await management('/maivita plan add 明天 09:00 10:00 联合验收计划')
            await management('/maivita plan remove '+receipt['target_id'])
            assert (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']['hard']=='HARD_SILENT'
            assert len(driver.calls)==1
            cases.append('native_activity_goal_evidence_and_plan_management_preserve_hard_silence')
            await command('/maihush 恢复聊天')
            assert (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']['hard']=='NONE'
            await command('/maihush 安静5分钟','10003')
            assert (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']['hard']=='NONE'
            await command('/maihush 安静5分钟')
            assert (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']['hard']=='HARD_SILENT'
            cases.append('native_finite_Chinese_hush_aliases_preserve_local_admin_boundary')
            # Early author/service denials must produce normal-send receipts.
            # No permission, Hush bypass or synthetic owner evidence is added.
            before=len(driver.calls)
            await command('/maiidentity relations','10004')
            assert len(driver.calls)==before
            await command('/maihush 恢复聊天')
            await command('/maiidentity relations','10004')
            assert len(driver.calls)==before+1 and '此命令不可用' in driver.texts[-1],driver.texts[-3:]
            cases.append('native_identity_denial_receipt_visible_when_allowed_and_blocked_by_hard_silence')

    finally:
        await supervisor.stop(); engine.dispose()
    return {'passed':True,'host':os.environ['MAIBOT_HOST_VERSION'],'sdk':importlib.metadata.version('maibot-plugin-sdk'),'cases':cases,'counted_driver_deliveries':len(driver.calls),
        'synthetic_model_calls':len(model_calls),'real_model_requests':0,'real_platform_sends':0,
        'life_input':'synthetic offline engine fixture; actual runtime deadline recovery',
        'guarantee':'registered guards on normal SendService; no production or H03 acceptance'}


if __name__=='__main__':
    try:
        work=Path(os.environ['MAIBOT_INSTANCE_DIR'])
        result=asyncio.run(asyncio.wait_for(run(work),110))
        (work/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    finally:
        from src.common.logger import shutdown_logging
        shutdown_logging()
