"""Five real Runner plugins with synthetic model/platform IO and real SDK RPC."""
import asyncio
import importlib.metadata
import json
import os
import time
import uuid
import tomli_w

VITA='davidblackcn.maibot-maivita-plugin'
COORD='davidblackcn.maibot-maicoordinate-plugin'
TRACE='Rabbit-Jia-Er.MaiTrace'


async def space_cases(work,supervisor,call,configs,model_calls,vita_path):
    from maivita.life.domain.clock import SystemClock
    from maivita.life.domain.commands import Command
    from maivita.life.life_state.engine import LifeStateEngine
    from maivita.life.storage.life_database import LifeDatabase
    assert TRACE in supervisor._registered_plugins,supervisor._registered_plugins
    descriptor=await call(TRACE,'get_space_contract')
    assert descriptor['ready'],descriptor
    context=await call(VITA,'get_public_life_context')
    assert context['ok'] and context['data']['activity']['duration_minutes']>=20,context
    await asyncio.sleep(1.2)
    status=(await call(COORD,'get_status'))['data']
    assert status['space']['last'] is None and status['health']=='WAITING_FOR_MANAGED_GROUP',status
    # End a continuously observed synthetic activity AFTER Coordinate's admission
    # watermark. Domain commands run only while the actual Vita Runner is stopped.
    assert (await supervisor.unload_plugins([VITA],reason='offline-space-fixture')).success
    engine=LifeStateEngine(LifeDatabase(vita_path,clock=SystemClock()))
    engine.execute(Command(kind='end_activity',payload={},source='authorized',idempotency_key=uuid.uuid4().hex,
        expected_state_version=engine.state().state_version))
    assert await supervisor.reload_plugins([VITA],reason='offline-space-fixture-finished')
    deadline=time.monotonic()+40
    saw_inflight=False
    while time.monotonic()<deadline:
        status=(await call(COORD,'get_status'))['data']['space']
        last=status.get('last')
        if last and last['status']=='submitted':
            saw_inflight=True
            before=time.monotonic();assert (await call(VITA,'get_public_life_context'))['ok']
            assert time.monotonic()-before<2
        if last and last['status']=='success':break
        await asyncio.sleep(.2)
    else:raise AssertionError(status)
    assert saw_inflight and json.loads(last['result'])['tid']=='native-space-tid',status
    # Public browse endpoint uses actual Host registration and cross-process RPC.
    reply=await supervisor.invoke_api(TRACE,'browse_once_api',args={'max_feeds':5,'interaction_whitelist':['10001']},timeout_ms=60000)
    assert reply.error is None and reply.payload['success'],reply
    assert reply.payload['result']['status']=='success',reply
    calls=work/'data/plugins'/TRACE/'native-space-calls.jsonl'
    kinds=[json.loads(line)['kind'] for line in calls.read_text('utf-8').splitlines()]
    for kind in ('published','read','like','comment','reply'):assert kinds.count(kind)==1,(kind,kinds)
    before=len(kinds)
    assert await supervisor.reload_plugins([COORD],reason='space-restart-dedup')
    await asyncio.sleep(1.2)
    assert len(calls.read_text('utf-8').splitlines())==before
    cfg=configs['maibot-maicoordinate-plugin'];cfg['space']['mode']='shadow'
    (work/'plugins/maibot-maicoordinate-plugin/config.toml').write_text(tomli_w.dumps(cfg),encoding='utf-8')
    assert await supervisor.reload_plugins([COORD],reason='space-config-reload')
    assert (await call(COORD,'get_status'))['data']['space']['mode']=='shadow'
    probe_verified=False
    if os.environ.get('JOINT_SERVER_PROBE')=='1':
        import tomllib
        probe_id='davidblackcn.phase1-server-probe'
        path=work/'plugins/phase1-server-probe/config.toml'
        probe_cfg=tomllib.loads(path.read_text('utf-8'));probe_cfg['plugin']['enabled']=True
        path.write_text(tomli_w.dumps(probe_cfg),encoding='utf-8')
        assert await supervisor.reload_plugins([probe_id],reason='read-only-server-tool-test')
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            reports=list((work/'data/plugins'/probe_id).glob('*.json'))
            if reports:break
            await asyncio.sleep(.1)
        else:raise AssertionError('No server probe report')
        report=json.loads(reports[0].read_text('utf-8'))
        assert report['status']=='finished' and report['result']['writes_requested'] is False,report
        assert len(calls.read_text('utf-8').splitlines())==before
        assert (await supervisor.unload_plugins([probe_id],reason='server-tool-finished')).success
        probe_verified=True
    assert (await supervisor.unload_plugins([COORD],reason='space-store-close')).success
    # Closed connection and writer lock permit opening after native unload.
    import sqlite3
    with sqlite3.connect(work/'data/plugins'/COORD/'coordinate.sqlite3') as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    return {'passed':True,'host':os.environ['MAIBOT_HOST_VERSION'],'sdk':importlib.metadata.version('maibot-plugin-sdk'),
        'cases':['five_real_plugins_registered','public_life_to_generation_to_space_publication',
            'space_without_group_routes','long_rpc_over_two_seconds_does_not_block_status',
            'real_api_browse_like_comment_self_reply','restart_does_not_replay','native_config_reload_and_unload'],
        'platform_fixture_calls':kinds,'synthetic_model_calls':len(model_calls),'server_readonly_probe_verified':probe_verified,
        'real_model_requests':0,'real_platform_sends':0,'fixture':'native_fixture.py replaces platform IO only'}
