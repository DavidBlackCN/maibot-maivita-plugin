import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maicoordinate.config import Settings,Route,VITA,HUSH,IDENTITY,HOST,SDK
from maicoordinate.contracts import SocialEvent,envelope,Unavailable
from maicoordinate.service import Coordinator
from maicoordinate.store import Store


def route():
    return Route(route_id='test',instance_id='isolated',adapter_namespace='adapter',bot_account_id='10001',
                 route_scope='scope',group_id='20001',stream_id='stream')


def settings(mode='shadow'):
    return Settings(mode=mode,model_task='test-task',routes=[route()])


def event(now=100000.,id='event-1',version=1):
    iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
    return {'contract_version':'1.0.0','event_id':id,'character_id':'default','event_type':'life.wakeup',
        'state_version':version,'event_index':0,'occurred_at':iso(now),'recorded_at':iso(now),
        'expires_at':iso(now+300),'visibility':'public_summary','summary':'A public wakeup.'}


def wrap(data,raw=False):
    return {'success':True,'result':data if raw else {'api_version':1,'ok':True,'data':data,'error':None}}


class HostDouble:
    def __init__(self):
        self.now=100000.; self.phase='awake'; self.hard='NONE'; self.soft='NORMAL'; self.version=1
        self.persona_digest='a'*64;self.social_revision='a'*64
        self.api=self; self.llm=self; self.send=SimpleNamespace(text=self.send_text)
        self.events=[event()]; self.sent=[]; self.generated=0; self.change=False; self.timeout=False; self.bad_route=False
        self.send_result={'success':True,'sent':True,'message_id':'test-id'}
        self.apis={VITA:['get_engine_status','get_life_state','get_public_events','get_public_route_scope','get_integration_contract','get_public_persona'],
                   HUSH:['get_contract','get_public_constraints'],IDENTITY:['get_contract','get_public_social_context']}

    async def list(self,plugin_id):
        return {'success':True,'apis':[{'plugin_id':plugin_id,'name':m,'version':'1'} for m in self.apis[plugin_id]]}

    async def call(self,name,version,**args):
        assert version=='1'
        plugin,method=name.rsplit('.',1)
        if method=='get_integration_contract': return wrap({'contract_version':'1.0.0','host':HOST,'sdk':SDK,'plugin_version':'0.4.1','features':['public_persona_v1','public_events_v1']},True)
        if method=='get_contract':
            return wrap({'plugin_id':plugin,'plugin_version':'0.3.0' if plugin==HUSH else '3.1.1',
                'contract_version':'1.1.0','api_major':'1','features':['public_social_context_v1'],'host':HOST,'sdk':SDK,'ready':True})
        if method=='get_public_persona':return wrap({'text':'shared persona','digest':self.persona_digest})
        if method=='get_public_social_context':return wrap({'stream_id':args['stream_id'],'revision':self.social_revision,'style':'neutral',
            'counts':{'friend':0,'family':0,'partner':0},'relation_state':'none_confirmed','completeness':'public_group_scope_only','rule':'No authority or intimacy inference.'})
        if method=='get_engine_status': return wrap({'ready':True,'engine':'life'},True)
        if method=='get_life_state':
            value=wrap({'character_id':'default','sleep_phase':self.phase,'state_version':self.version,'plan_version':0,'next_deadline_at':None})
            value['result']['as_of']=datetime.fromtimestamp(self.now,timezone.utc).isoformat(); return value
        if method=='get_public_constraints': return wrap({'stream_id':'stream','route_fingerprint':'wrong' if self.bad_route else route().hush_fingerprint(),
            'hard':self.hard,'soft':self.soft,'health':'READY','state_version':1,'config_revision':1,'as_of':self.now,'valid_until':self.now+5})
        if method=='get_public_route_scope': return wrap({'stream_id':'stream','character_id':'default','guarded':True,'config_digest':'a'*64})
        if method=='get_public_events':
            values=[e for e in self.events if e['state_version']>args['after_version']]
            return wrap({'events':values,'cursor':[values[-1]['state_version'],0] if values else [args['after_version'],args['after_index']]})
        raise AssertionError(name)

    async def generate(self,*args,**kwargs):
        self.generated+=1
        if self.change: self.version+=1
        return {'success':True,'response':'我醒啦。'}

    async def send_text(self,text,stream,**kwargs):
        self.sent.append((text,stream))
        if self.timeout: raise TimeoutError('ambiguous')
        return self.send_result


@pytest.fixture
def system(tmp_path):
    cfg=settings('controlled'); host=HostDouble(); store=Store(tmp_path/'flow.db',cfg)
    yield Coordinator(host,cfg,store,wall=lambda:host.now),host,store
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,hard,soft,expected',[
    ('awake','NONE','NORMAL',True),('sleeping','NONE','NORMAL',False),('temporarily_awake','NONE','NORMAL',False),
    ('waking','NONE','NORMAL',False),('awake','HARD_SILENT','NORMAL',False),('awake','NONE','SOFT_SILENT',False),
    ('awake','NONE','RESTRAINED',False),('awake','NONE','RECOVERING',False)])
async def test_intersection_matrix(system,phase,hard,soft,expected):
    service,host,store=system; host.phase=phase; host.hard=hard; host.soft=soft
    await service.cycle()
    assert bool(host.sent)==expected
    assert store.conn.execute('SELECT count(*) FROM actions').fetchone()[0]==1
    await service.cycle(); assert len(host.sent)==int(expected)


@pytest.mark.asyncio
async def test_shadow_has_no_generation_send_or_privileged_calls(tmp_path):
    cfg=settings(); host=HostDouble(); store=Store(tmp_path/'flow.db',cfg)
    try:
        await Coordinator(host,cfg,store,wall=lambda:host.now).cycle()
        assert not host.sent and host.generated==0
        assert store.conn.execute('SELECT status FROM actions').fetchone()[0]=='shadow'
    finally: store.close()


@pytest.mark.asyncio
async def test_generation_requires_fresh_same_version_and_route(system):
    service,host,store=system; host.change=True
    await service.cycle(); assert not host.sent and host.generated==1
    assert store.conn.execute('SELECT reason FROM actions').fetchone()[0]=='STATE_CHANGED'


@pytest.mark.asyncio
async def test_wrong_route_unknown_and_expired_do_not_send(system):
    service,host,store=system; host.bad_route=True
    await service.cycle(); assert not host.sent and host.generated==0
    assert store.conn.execute('SELECT reason FROM actions').fetchone()[0]=='HUSH_ROUTE_MISMATCH'


@pytest.mark.asyncio
async def test_ambiguous_send_is_durable_and_not_retried(system):
    service,host,store=system; host.timeout=True
    await service.cycle(); await service.cycle()
    assert len(host.sent)==1
    assert store.conn.execute('SELECT status FROM actions').fetchone()[0]=='unknown'
    path=store.path; cfg=service.settings; store.close()
    reopened=Store(path,cfg)
    try:
        await Coordinator(host,cfg,reopened,wall=lambda:host.now).cycle()
        assert len(host.sent)==1
    finally: reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('result,status',[
    ({'success':True,'sent':True,'message_id':'some'},'sent_some'),
    ({'success':False,'sent':False},'not_sent'),({'success':True},'unknown'),(True,'unknown')])
async def test_send_result_is_not_inferred_from_truthiness(system,result,status):
    service,host,store=system; host.send_result=result
    await service.cycle()
    assert store.conn.execute('SELECT status FROM actions').fetchone()[0]==status


def test_two_layer_results_and_strict_event():
    for value in ({},{'success':True,'result':{}},{'success':True,'result':{'api_version':1,'ok':False,'data':{},'error':None}}):
        with pytest.raises(Unavailable): envelope(value)
    for patch in ({'owner':True},{'occurred_at':'2026-10-24T12:00:00'},{'contract_version':'2.0.0'},{'state_version':True}):
        with pytest.raises(ValueError): SocialEvent.from_wire(event()|patch)


def test_ingest_atomic_capacity_conflict_and_restart(tmp_path):
    cfg=settings(); store=Store(tmp_path/'flow.db',cfg)
    try:
        one=SocialEvent.from_wire(event()); two=SocialEvent.from_wire(event(id='two',version=2))
        with pytest.raises(ValueError,match='QUEUE_FULL'):
            store.ingest(VITA,[one,two],[2,0],cfg.routes,100000,1)
        assert store.get('cursor',[0,-1])==[0,-1]
        assert store.conn.execute('SELECT count(*) FROM actions').fetchone()[0]==0
        store.ingest(VITA,[one],[1,0],cfg.routes,100000,1)
        store.ingest(VITA,[one],[1,0],cfg.routes,100000,1)
        with pytest.raises(ValueError,match='IDEMPOTENCY'):
            store.ingest(VITA,[SocialEvent.from_wire(event()|{'summary':'changed'})],[1,0],cfg.routes,100000,1)
        store.finish('event-1','test','executing','attempt',100000)
        store.close(); store=Store(tmp_path/'flow.db',cfg)
        assert store.conn.execute('SELECT status FROM actions').fetchone()[0]=='unknown'
        with pytest.raises((OSError,ValueError)): Store(tmp_path/'flow.db',cfg)
    finally: store.close()


@pytest.mark.asyncio
async def test_sdk_ports_preserve_outer_failure_and_send_evidence():
    from maibot_sdk.context import PluginContext
    from maicoordinate.ports import SDKPorts
    async def rpc(method,plugin_id,payload,**kwargs):
        assert method=='cap.call' and plugin_id=='test.coordinator'
        if payload['capability']=='api.call':
            return {'success':False,'result':{'api_version':1,'ok':True,'data':{},'error':None}}
        return {'success':False,'sent':False,'message_id':None}
    ctx=PluginContext('test.coordinator',rpc_call=rpc)
    ports=SDKPorts(ctx)
    with pytest.raises(Unavailable): envelope(await ports.api.call(VITA+'.get_life_state',version='1'))
    assert await ports.send.text('test','stream',return_details=True)=={'success':False,'sent':False,'message_id':None}


@pytest.mark.asyncio
async def test_expired_and_backlog_events_never_burst(system):
    service,host,store=system
    host.now+=301
    await service.cycle()
    assert not host.sent and host.generated==0
    host.events=[event(now=host.now,id='new-'+str(i),version=i) for i in range(2,6)]
    host.version=5
    await service.cycle()
    assert len(host.sent)==1
    assert store.conn.execute("SELECT count(*) FROM actions WHERE reason='SUPERSEDED'").fetchone()[0]==3


@pytest.mark.asyncio
async def test_missing_service_and_bad_cursor_do_not_advance_or_send(system):
    service,host,store=system
    host.apis[HUSH]=[]
    with pytest.raises(Unavailable): await service.cycle()
    assert store.get('cursor',[0,-1])==[0,-1] and not host.sent
    host.apis[HUSH]=['get_contract','get_public_constraints']
    host.events=[event(version=2),event(id='older',version=1)]
    with pytest.raises(Unavailable): await service.cycle()
    assert store.get('cursor',[0,-1])==[0,-1]


@pytest.mark.asyncio
async def test_clock_rollback_preserves_high_watermark(system):
    service,host,store=system
    await service.cycle(); host.now-=100
    with pytest.raises(Unavailable,match='CLOCK_ROLLBACK'): await service.cycle()
    assert len(host.sent)==1 and store.get('last_wall',0)==100000


def test_route_rebinding_refused_after_restart(tmp_path):
    cfg=settings(); path=tmp_path/'flow.db'; store=Store(path,cfg); store.close()
    changed=cfg.model_copy(update={'routes':[route().model_copy(update={'bot_account_id':'90001'})]})
    with pytest.raises(ValueError,match='OFFLINE_ROUTE_MIGRATION'): Store(path,changed)
