from types import SimpleNamespace
import pytest
from test_coordinator import route,wrap
from maicoordinate.config import Settings
from maicoordinate.plugin import MaiCoordinatePlugin
from maicoordinate.store import Store
from maicoordinate.contracts import Unavailable

def message(scope=None):
    return {'platform':'qq','account_id':'10001','scope':scope,'session_id':'stream',
            'message_info':{'group_info':{'group_id':'20001'}}}

@pytest.mark.asyncio
async def test_auto_route_requires_matching_hush_and_persists_without_authority(tmp_path):
    p=MaiCoordinatePlugin(); p.active=Settings(); p.automatic=True
    p.store=Store(tmp_path/'flow.db',p.active)
    async def call(*args,**kwargs):
        return {'api_version':1,'ok':True,'data':{k:v for k,v in route().model_copy(update={'route_scope':''}).model_dump().items()
                 if k in ('platform','adapter_namespace','bot_account_id','route_scope','group_id','stream_id')},'error':None}
    # service.call preserves Host envelope; production decoder requires it.
    async def outer_call(*args,**kwargs): return {'success':True,'result':await call()}
    p.service=SimpleNamespace(call=outer_call,settings=p.active)
    try:
        await p.observe_route(message=message()); await p.discover_observed()
        assert p.active.routes[0].route_scope=='' and p.active.mode=='shadow'
        assert p.store.get('observed_routes',[])[0]['stream_id']=='stream'
        assert p.store.get('route_since:stream',0)>0
        await p.observe_route(message=message()); assert not p.observed
    finally: p.store.close()

@pytest.mark.asyncio
async def test_auto_route_rejects_named_vs_null_mismatch(tmp_path):
    p=MaiCoordinatePlugin();p.active=Settings();p.automatic=True;p.store=Store(tmp_path/'flow.db',p.active)
    async def call(*args,**kwargs): return wrap(route().model_dump())
    p.service=SimpleNamespace(call=call,settings=p.active)
    try:
        await p.observe_route(message=message())
        with pytest.raises(Unavailable,match='MISMATCH'): await p.discover_observed()
        assert not p.active.routes and not p.store.get('observed_routes',[])
    finally:p.store.close()
