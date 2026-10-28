"""Faults at real workflow boundaries; host doubles are not native acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from test_coordinator import HostDouble, event, route, settings, wrap
from maicoordinate.config import Settings, VITA, HUSH, IDENTITY
from maicoordinate.contracts import SocialEvent, Unavailable, decide
from maicoordinate.plugin import MaiCoordinatePlugin
from maicoordinate.service import Coordinator
from maicoordinate.store import Store


@pytest.fixture
def system(tmp_path):
    cfg=settings('controlled'); host=HostDouble(); store=Store(tmp_path/'flow.db',cfg)
    yield Coordinator(host,cfg,store,wall=lambda:host.now),host,store
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['invalid','exception','cancel','expiry','contract','hush','route','life_config'])
async def test_generation_boundary_faults_never_send(system,fault):
    service,host,store=system
    original=host.call
    async def changed(name,version,**args):
        value=await original(name,version,**args)
        if fault=='contract' and name.endswith('get_integration_contract'):
            value['result']['sdk']='unsupported-test-version'
        if fault=='life_config' and name.endswith('get_public_route_scope'):
            value['result']['data']['config_digest']='b'*64
        return value
    async def generate(*args,**kwargs):
        host.generated+=1
        if fault=='invalid': return {'success':True,'response':' '}
        if fault=='exception': raise TimeoutError()
        if fault=='cancel': raise asyncio.CancelledError()
        if fault=='expiry': host.now+=301
        if fault=='hush': host.hard='HARD_SILENT'
        if fault=='route': host.bad_route=True
        host.call=changed
        return {'success':True,'response':'hello'}
    host.generate=generate
    # A child cancellation is deliberately joined by TaskGroup.
    await service.cycle()
    assert not host.sent
    assert store.conn.execute('SELECT status FROM actions').fetchone()[0] in ('failed','cancelled','expired')
    assert store.conn.execute('SELECT until_at FROM cooldown').fetchone()[0]>100000


@pytest.mark.asyncio
async def test_failed_generation_budget_survives_restart_and_shadow_obeys_it(system):
    service,host,store=system
    async def fail(*args,**kwargs):
        host.generated+=1
        raise TimeoutError()
    host.generate=fail
    await service.cycle()
    path=store.path; cfg=service.settings; store.close()
    reopened=Store(path,cfg)
    try:
        host.events.append(event(id='second',version=2)); host.version=2
        shadow=Coordinator(host,settings('shadow'),reopened,wall=lambda:host.now)
        await shadow.cycle()
        assert host.generated==1 and not host.sent
        assert reopened.conn.execute("SELECT reason FROM actions WHERE event_id='second'").fetchone()[0]=='COOLDOWN'
        host.now+=3601; host.version=3; host.events.append(event(now=host.now,id='third',version=3))
        await Coordinator(host,cfg,reopened,wall=lambda:host.now).cycle()
        assert host.generated==2
    finally: reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['generate','send'])
async def test_cancel_joins_inflight_and_persists_result(system,stage):
    service,host,store=system; entered=asyncio.Event(); exited=asyncio.Event()
    async def blocked(*args,**kwargs):
        entered.set()
        try: await asyncio.Event().wait()
        finally: exited.set()
    if stage=='generate': host.generate=blocked
    else: host.send.text=blocked
    task=asyncio.create_task(service.cycle())
    await asyncio.wait_for(entered.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert exited.is_set()
    assert store.conn.execute('SELECT status FROM actions').fetchone()[0]==('unknown' if stage=='send' else 'failed')
    await service.cycle()
    assert store.conn.execute('SELECT count(*) FROM actions').fetchone()[0]==1


@pytest.mark.asyncio
async def test_concurrent_cycles_do_not_duplicate_side_effect(system):
    service,host,store=system
    await asyncio.gather(*(service.cycle() for _ in range(10)))
    assert host.generated==1 and len(host.sent)==1


def test_cleanup_retains_unknown_result_and_dedup_and_enforces_budget(tmp_path):
    cfg=settings(); store=Store(tmp_path/'flow.db',cfg)
    try:
        one=SocialEvent.from_wire(event())
        store.ingest(VITA,[one],[1,0],cfg.routes,100000,256)
        store.finish(one.event_id,'test','unknown','SEND_OUTCOME_UNKNOWN',100000,{'life':1},'attempt')
        store.maintain(100000+8*86400)
        row=store.conn.execute('SELECT * FROM actions').fetchone()
        assert row['payload']=='{}' and row['status']=='unknown' and row['attempt']=='attempt'
        assert json.loads(row['basis'])=={'life':1}
        store.ingest(VITA,[one],[1,0],cfg.routes,100000+8*86400,256)
        with pytest.raises(ValueError,match='IDEMPOTENCY'):
            store.ingest(VITA,[SocialEvent.from_wire(event()|{'summary':'different'})],[2,0],cfg.routes,100000,256)
        store.max_records=1  # Exercise a full store without filling thousands of receipts.
        with pytest.raises(ValueError,match='STORAGE_FULL'):
            store.ingest(VITA,[SocialEvent.from_wire(event(id='next',version=2))],[2,0],cfg.routes,100000,256)
        assert store.get('cursor',None)==[1,0]
        assert store.conn.execute('SELECT count(*) FROM actions').fetchone()[0]==1
    finally: store.close()


@pytest.mark.asyncio
async def test_full_storage_still_drains_pending_and_reports_degraded(system):
    service,host,store=system
    store.ingest(VITA,[SocialEvent.from_wire(event())],[1,0],service.settings.routes,host.now,256)
    store.max_records=1; host.events.append(event(id='next',version=2)); host.version=2
    await service.cycle()
    assert len(host.sent)==1 and store.get('cursor',None)==[1,0]
    assert service.health=='DEGRADED' and service.last_error=='STORAGE_FULL'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['missing','legacy','stale','unready','badhealth','envelope'])
async def test_public_snapshot_never_assumes_normal(system,fault):
    service,host,store=system; original=host.call
    async def changed(name,version,**args):
        value=await original(name,version,**args)
        if name.endswith('get_engine_status') and fault=='legacy': value['result']['engine']='legacy'
        if name.endswith('get_contract') and name.startswith(IDENTITY) and fault=='unready': value['result']['data']['ready']=False
        if name.endswith('get_public_constraints'):
            if fault=='stale': value['result']['data']['valid_until']=host.now-1
            if fault=='badhealth': value['result']['data'].pop('health')
            if fault=='envelope': value['success']=False
        return value
    host.call=changed
    if fault=='missing': host.apis[HUSH]=[]
    snapshot=await service.snapshot(route())
    assert decide(snapshot,host.now)!='ELIGIBLE'
    assert not host.sent and host.generated==0
    assert store.get('cursor',None) is None


@pytest.mark.asyncio
async def test_other_route_progress_and_taskgroup_joins_on_failure(tmp_path):
    first=route(); second=first.model_copy(update={'route_id':'second','stream_id':'second-stream','group_id':'20002'})
    cfg=settings('controlled').model_copy(update={'routes':[first,second]})
    store=Store(tmp_path/'flow.db',cfg); host=HostDouble(); service=Coordinator(host,cfg,store,wall=lambda:host.now)
    slow=asyncio.Event(); fast=asyncio.Event(); joined=asyncio.Event()
    async def process(r):
        if r.route_id=='test':
            slow.set()
            try: await asyncio.Event().wait()
            finally: joined.set()
        else:
            await slow.wait(); fast.set(); raise RuntimeError('injected sibling failure')
    service.process_route=process
    try:
        with pytest.raises(ExceptionGroup): await asyncio.wait_for(service.cycle(),1)
        assert fast.is_set() and joined.is_set()
    finally: store.close()


@pytest.mark.asyncio
async def test_readonly_result_and_status_and_config_stop(system):
    service,host,store=system
    await service.cycle()
    plugin=MaiCoordinatePlugin(); plugin.service=service; plugin.store=store; plugin.active=service.settings
    result=await plugin.get_action_result(route_id='test',event_id='event-1',owner=True,proof='fake')
    assert result['data']['status']=='sent_some' and result['data']['attempt_id']
    assert 'payload' not in result['data']
    assert not (await plugin.get_action_result(route_id='other',event_id='event-1'))['ok']
    assert (await plugin.get_status())['data']['H03'] is False
    with pytest.raises(RuntimeError,match='RELOAD_REQUIRED'):
        await plugin.on_config_update('self',{},'2')
    assert plugin.service is None and store.conn is None
    assert (await plugin.get_status())['data']['health']=='NOT_READY'


@pytest.mark.parametrize('patch',[
    {'privileged_writes':True},{'mode':'controlled','model_task':''},
    {'routes':[route().model_dump(),route().model_dump()]},
    {'plugin':{'enabled':'true'}}, {'routes':[route().model_dump()|{'route_scope':' '} ]},
    {'queue_capacity':1024,'max_records':256}, {'vita_plugin':'attacker.plugin'}])
def test_configuration_fails_closed(patch):
    with pytest.raises(ValueError): Settings.model_validate(patch)


def test_sdk_editor_schema_drafts_cannot_be_runtime_routes():
    schema=MaiCoordinatePlugin.build_config_schema(plugin_id='test.coordinator')
    assert schema and 'route_id' in json.dumps(schema)
    with pytest.raises(ValueError): Settings.model_validate({'routes':[{}]})
    assert Settings().routes==[] and Settings().plugin.enabled is True and Settings().mode=='shadow'


@pytest.mark.asyncio
async def test_slow_generation_does_not_block_other_route(tmp_path):
    routes=[route(),route().model_copy(update={'route_id':'second','stream_id':'second-stream','group_id':'20002'})]
    cfg=settings('controlled').model_copy(update={'routes':routes})
    host=HostDouble(); original=host.call
    async def routed(name,version,**args):
        value=await original(name,version,**args)
        if name.endswith(('get_public_constraints','get_public_route_scope')):
            r=next(r for r in routes if r.stream_id==args['stream_id'])
            value['result']['data']['stream_id']=r.stream_id
            if name.endswith('get_public_constraints'): value['result']['data']['route_fingerprint']=r.hush_fingerprint()
        return value
    host.call=routed
    blocked=asyncio.Event(); release=asyncio.Event(); fast_sent=asyncio.Event()
    async def generate(*args,**kwargs):
        host.generated+=1
        if host.generated==1: blocked.set(); await release.wait()
        return {'success':True,'response':'hello'}
    async def send(text,stream,**kwargs):
        host.sent.append(stream); fast_sent.set()
        return {'success':True,'sent':True}
    host.generate=generate; host.send.text=send
    store=Store(tmp_path/'flow.db',cfg); service=Coordinator(host,cfg,store,wall=lambda:host.now)
    task=asyncio.create_task(service.cycle())
    try:
        await asyncio.wait_for(blocked.wait(),1)
        await asyncio.wait_for(fast_sent.wait(),1)
        assert len(host.sent)==1 and not task.done()
        release.set(); await task
        assert set(host.sent)=={r.stream_id for r in routes}
        assert store.conn.execute("SELECT count(*) FROM actions WHERE status='sent_some'").fetchone()[0]==2
    finally:
        task.cancel(); await asyncio.gather(task,return_exceptions=True); store.close()


@pytest.mark.asyncio
async def test_bounded_pages_advance_atomically_without_burst(tmp_path):
    cfg=settings('controlled').model_copy(update={'queue_capacity':1})
    host=HostDouble(); host.version=3
    host.events=[event(id=str(i),version=i) for i in range(1,4)]
    original=host.call
    async def paged(name,version,**args):
        if name.endswith('get_public_events'):
            rows=[e for e in host.events if e['state_version']>args['after_version']][:args['limit']]
            return wrap({'events':rows,'cursor':[rows[-1]['state_version'],0] if rows else [args['after_version'],args['after_index']]})
        return await original(name,version,**args)
    host.call=paged; store=Store(tmp_path/'flow.db',cfg)
    try:
        service=Coordinator(host,cfg,store,wall=lambda:host.now)
        for expected in (1,2,3):
            await service.cycle(); assert store.get('cursor',None)==[expected,0]
        assert len(host.sent)==1 and host.generated==1
    finally: store.close()


def test_unknown_database_refuses_without_resetting_records(tmp_path):
    import sqlite3
    path=tmp_path/'flow.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE foreign_data(value TEXT)')
        conn.execute("INSERT INTO foreign_data VALUES ('keep')")
    with pytest.raises(ValueError,match='UNSUPPORTED_DATABASE'): Store(path,settings())
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT value FROM foreign_data').fetchone()[0]=='keep'
