import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maicoordinate.config import Settings
from maicoordinate.space import SpaceCoordinator,ADAPTATION
from maicoordinate.store import Store


class Port:
    def __init__(self):
        self.now=1800000000.; self.awake=True;self.activity=None;self.calls=[];self.block=None
        self.result={'result':True,'status':'success','tid':'test-id'}
    async def call(self,name,**args):
        if name.endswith('get_space_contract'):
            return {'success':True,'result':{'adaptation':ADAPTATION,'ready':True,'interaction_whitelist':['12345'],'budgets':{'post':30,'browse':60}}}
        assert name.endswith('get_public_life_context')
        return {'success':True,'result':{'ok':True,'api_version':1,'data':{
            'as_of':datetime.fromtimestamp(self.now,timezone.utc).isoformat(),'awake':self.awake,
            'availability':{'state':'free'},'activity':self.activity,'history':[]}}}
    async def call_space(self,name,**args):
        self.calls.append((name,args))
        if self.block: await self.block.wait()
        return {'success':True,'result':self.result}
    def material(self,aid='activity'):
        self.activity={'activity_id':aid,'name':'reading','visibility':'public_summary','continuity_unknown':False,
            'started_at':datetime.fromtimestamp(self.now,timezone.utc).isoformat(),'ended_at':None,'duration_minutes':20}
        self.now+=1200


def setup(tmp_path,mode='controlled',whitelist=None):
    cfg=Settings(space={'mode':mode,'interaction_whitelist':whitelist or []})
    store=Store(tmp_path/'coordinate.db',cfg);port=Port()
    service=SpaceCoordinator(SimpleNamespace(api=port),cfg,store,wall=lambda:port.now)
    return service,store,port,cfg


@pytest.mark.asyncio
async def test_no_group_route_publishes_and_preserves_unknown(tmp_path):
    s,db,p,cfg=setup(tmp_path)
    try:
        p.material();p.result={'status':'unknown'};await s.cycle()
        assert len(p.calls)==1 and s.status()['limits']['post']['remaining']==1
        await s.cycle();assert len(p.calls)==1
        db.close();db=Store(tmp_path/'coordinate.db',cfg)
        s=SpaceCoordinator(SimpleNamespace(api=p),cfg,db,wall=lambda:p.now)
        await s.cycle();assert len(p.calls)==1
        assert s.status()['last']['status']=='unknown'
    finally: db.close()


@pytest.mark.asyncio
async def test_shadow_and_sleep_have_no_provider_side_effect(tmp_path):
    s,db,p,_=setup(tmp_path,'shadow')
    try:
        p.material();await s.cycle();assert not p.calls and s.reason=='SHADOW_NOT_EXECUTED'
        p.awake=False;await s.cycle();assert s.reason=='SLEEPING'
        p.awake=True;await s.cycle();assert not p.calls and 'NO_NEW' in s.reason
        assert not db.conn.execute('SELECT * FROM space_actions').fetchall()
    finally: db.close()


@pytest.mark.asyncio
async def test_browse_whitelist_limits_and_independent_status(tmp_path):
    s,db,p,_=setup(tmp_path,whitelist=['12345'])
    try:
        p.now+=3601;await s.cycle();assert p.calls[0][0].endswith('browse_once_api')
        assert p.calls[0][1]['max_feeds']==5
        await s.cycle();assert len(p.calls)==1
        p.now+=3601;p.block=asyncio.Event()
        task=asyncio.create_task(s.cycle());await asyncio.sleep(.02)
        assert s.status()['last']['status']=='submitted'
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        assert s.status()['last']['status']=='unknown'
    finally: db.close()


def test_old_config_disables_space_and_upgrade_is_idempotent(tmp_path):
    cfg=Settings.model_validate({'plugin':{'config_version':'1'}})
    assert cfg.space.mode=='disabled'
    assert Settings.model_validate(cfg.model_dump())==cfg
    assert Settings().space.mode=='shadow'


@pytest.mark.asyncio
async def test_exhausted_quota_waits_for_natural_day_and_status_explains_unknown(tmp_path):
    from maicoordinate.space import render_status
    from zoneinfo import ZoneInfo
    s,db,p,_=setup(tmp_path)
    try:
        p.material('first');p.result={'status':'unknown'};await s.cycle()
        p.now+=14401;p.material('second');await s.cycle()
        state=s.status();assert state['limits']['post']['remaining']==0
        local=datetime.fromtimestamp(p.now,ZoneInfo(s.config.timezone))
        next_day=datetime.fromtimestamp(state['limits']['post']['next_at'],ZoneInfo(s.config.timezone))
        assert next_day.date()>local.date()
        text=render_status(state)
        assert '待核对' in text and '素材：reading' in text and '今日剩余 0 次' in text
    finally:db.close()
