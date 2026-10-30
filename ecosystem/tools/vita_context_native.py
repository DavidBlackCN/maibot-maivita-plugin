"""Focused real Host/Runner acceptance; only isolated synthetic life and IO."""
import asyncio
import importlib.metadata
import json
import os
import uuid
import tomli_w

VITA='davidblackcn.maibot-maivita-plugin'


async def context_cases(work,supervisor,manager,call,configs,stream,vita_path,model_calls,driver):
    from maivita.life.domain.clock import SystemClock
    from maivita.life.domain.commands import Command
    from maivita.life.life_state.engine import LifeStateEngine
    from maivita.life.storage.life_database import LifeDatabase
    from maivita.life.domain.segment import SchedulerConfig
    from src.plugin_runtime.hook_payloads import serialize_prompt_items,deserialize_prompt_items
    from src.llm_models.payload_content.context_item import UserMessageItem,ContextItemMeta,ContextTextPart
    from src.llm_models.payload_content.context_protocol import ContextProtocolMode
    cases=[]
    assert (await supervisor.unload_plugins([VITA],reason='context-offline-fixture')).success
    engine=LifeStateEngine(LifeDatabase(vita_path,clock=SystemClock()))
    engine.scheduler=SchedulerConfig(enabled=True)
    def execute(kind,payload):
        return engine.execute(Command(kind=kind,payload=payload,source='authorized',idempotency_key=uuid.uuid4().hex,
            expected_state_version=engine.state().state_version))
    if engine.state().active_sleep_session_id:
        execute('wake',{})
    execute('start_activity',{'name':'PRIVATE_NATIVE_SENTINEL','category':'study','visibility':'restricted'})
    await asyncio.sleep(.02)
    execute('advance_segment',{'event':'在公园散步','category':'travel','location':'公园','emotion':'轻松',
        'minutes':60,'continue':False,'sleep':False})
    await asyncio.sleep(.02)
    execute('advance_segment',{'event':'在家读小说','category':'rest','location':'窗边沙发','emotion':'放松',
        'minutes':60,'continue':False,'sleep':False})
    assert await supervisor.reload_plugins([VITA],reason='context-fixture-ready')
    facts=(await call(VITA,'get_public_life_context'))['data']
    assert facts['activity']['location']=='窗边沙发',facts
    assert 'PRIVATE_NATIVE_SENTINEL' not in json.dumps(facts)
    inspected=await supervisor.inspect_plugin_config(VITA)
    assert not inspected.config_error,inspected.config_error
    sections=inspected.config_schema['sections']
    assert sections['model']['fields']['timeout_seconds']['hidden']
    assert sections['scheduler']['collapsed']
    cases.append('real_public_api_and_webui_schema')

    before=len(model_calls)
    reply=await manager.invoke_hook('maisaka.replyer.before_request',session_id=stream,extra_prompt='OTHER_PLUGIN_KEEP')
    assert not reply.errors and not reply.aborted,reply
    text=reply.kwargs['extra_prompt']
    for value in ('OTHER_PLUGIN_KEEP','窗边沙发','放松','在公园散步'):
        assert value in text,text
    assert 'PRIVATE_NATIVE_SENTINEL' not in text
    vita_text=text.split('[MaiVita I1 state]',1)[1].split('[/MaiVita I1 state]',1)[0]
    assert 'state_version' not in vita_text and 'activity_id' not in vita_text
    repeated=await manager.invoke_hook('maisaka.replyer.before_request',session_id=stream,
                                       extra_prompt=reply.kwargs['extra_prompt'])
    assert repeated.kwargs['extra_prompt'].count('[MaiVita I1 state]')==1
    cases.append('replyer_natural_public_context_preserves_other_plugins_and_deduplicates')

    original=[UserMessageItem(meta=ContextItemMeta.create(),parts=(ContextTextPart(text='synthetic user text'),))]
    payload=serialize_prompt_items(original)
    planner=await manager.invoke_hook('maisaka.planner.before_request',items=payload,item_schema_version=1,
                                      tool_definitions=[],session_id=stream)
    assert not planner.errors and not planner.aborted,planner
    restored=deserialize_prompt_items(planner.kwargs['items'],item_schema_version=1,
        mode=ContextProtocolMode.REQUEST_CONTEXT,original_items=original)
    assert restored[0]==original[0]
    assert '窗边沙发' in json.dumps(planner.kwargs,ensure_ascii=False)
    assert len(model_calls)==before and not driver.calls
    cases.append('typed_planner_roundtrip_without_model_or_platform_side_effects')

    cfg=configs['maibot-maivita-plugin']
    cfg['instance']['allowed_streams']=['different-isolated-stream']
    (work/'plugins/maibot-maivita-plugin/config.toml').write_text(tomli_w.dumps(cfg),encoding='utf8')
    assert await supervisor.reload_plugins([VITA],reason='context-range-reload')
    excluded=await manager.invoke_hook('maisaka.replyer.before_request',session_id=stream,extra_prompt='KEEP')
    assert '[MaiVita I1 state]' not in excluded.kwargs['extra_prompt']
    cfg['instance']['allowed_streams']=[stream]
    (work/'plugins/maibot-maivita-plugin/config.toml').write_text(tomli_w.dumps(cfg),encoding='utf8')
    assert await supervisor.reload_plugins([VITA],reason='context-range-restored')
    cases.append('config_reload_preserves_scope')
    assert (await supervisor.unload_plugins([VITA],reason='context-store-close')).success
    with engine.database.read_view() as u:
        assert u.connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    cases.append('native_unload_then_database_read')
    return {'passed':True,'host':os.environ['MAIBOT_HOST_VERSION'],
        'sdk':importlib.metadata.version('maibot-plugin-sdk'),'cases':cases,
        'real_model_requests':0,'real_platform_sends':0,'synthetic_model_calls':len(model_calls),
        'chat_sample':vita_text,'limits':'Synthetic life/IO only; real model quality and QQ experience not tested.'}
