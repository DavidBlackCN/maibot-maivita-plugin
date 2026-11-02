"""Public route snapshots use Host evidence and reject lifecycle races."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import r0_support
from maibot_autonomous_planning_plugin.maivita.host.entrypoint import create_plugin
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig


def configured(rows=None):
    p=create_plugin();p.ready=True
    p.active_config=IntegrationConfig()
    p.authority={'config_sha256':'digest'}
    p.delegate=SimpleNamespace(ready=True,read=AsyncMock(return_value={'ok':True,'data':{'value':'old'}}))
    row={'platform':'qq','session_id':'session','stream_id':'session','account_id':'123456789','scope':'scope'}
    get=AsyncMock(return_value=[row] if rows is None else rows)
    p._set_context(SimpleNamespace(chat=SimpleNamespace(get_all_streams=get)))
    return p,get,row


def test_scope_is_host_bound_small_snapshot_not_send_permit():
    async def run():
        p,get,row=configured()
        result=await p.get_public_route_scope(stream_id='session')
        assert result['ok']
        view=result['data']
        assert view['bot_account_id']==row['account_id'] and view['route_scope']=='scope'
        assert view['config_digest']=='digest' and view['guarded'] and view['send_permit'] is False
        assert (datetime.fromisoformat(view['snapshot_valid_until'])-datetime.fromisoformat(view['as_of'])).total_seconds()==5
        assert set(view)=={'stream_id','character_id','guarded','config_digest','platform','bot_account_id','route_scope','as_of','snapshot_valid_until','send_permit'}
        get.assert_awaited_once_with(platform='qq')
    asyncio.run(run())


@pytest.mark.parametrize('change',['missing','duplicate','account','scope','platform','session'])
def test_unknown_or_conflicting_host_route_is_never_guarded(change):
    async def run():
        p,get,row=configured()
        rows=[row.copy()]
        if change=='missing': rows=[]
        elif change=='duplicate': rows=rows*2
        else: rows[0][{'account':'account_id','scope':'scope','platform':'platform','session':'stream_id'}[change]]=123
        get.return_value=rows
        result=await p.get_public_route_scope(stream_id='session')
        assert not result['ok'] and result['data'] is None
        assert result['error']['reason']=='HOST_ROUTE_UNRESOLVED'
    asyncio.run(run())


def test_outside_scope_differs_from_lookup_failure_and_does_not_query_host():
    async def run():
        p,get,_=configured()
        p.active_config=IntegrationConfig.model_validate({'instance':{'allowed_streams':['different']}})
        result=await p.get_public_route_scope(stream_id='session')
        assert result['error']['reason']=='OUTSIDE_CONFIGURED_SCOPE'
        get.assert_not_awaited()
        for stream in (None,'',123,'x'*257):
            assert not (await p.get_public_route_scope(stream_id=stream))['ok']
        get.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('change',['unload','config','delegate'])
def test_route_reply_is_discarded_after_lifecycle_change(change):
    async def run():
        p,get,row=configured()
        async def query(**kwargs):
            if change=='unload': p.ready=False
            elif change=='config': p.active_config=p.active_config.model_copy()
            else: p.delegate=SimpleNamespace(ready=True)
            return [row]
        get.side_effect=query
        assert (await p.get_public_route_scope(stream_id='session'))['error']['code']=='ENGINE_UNAVAILABLE'
    asyncio.run(run())


def test_host_failure_and_caller_cancellation_do_not_become_permission():
    async def run():
        p,get,_=configured()
        get.side_effect=TimeoutError
        assert not (await p.get_public_route_scope(stream_id='session'))['ok']
        entered=asyncio.Event()
        async def stalled(**kwargs):
            entered.set();await asyncio.Event().wait()
        get.side_effect=stalled
        task=asyncio.create_task(p.get_public_route_scope(stream_id='session'))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        get.side_effect=None
        assert (await p.get_public_route_scope(stream_id='session'))['ok']
    asyncio.run(run())


def test_life_query_does_not_return_old_data_after_reload():
    async def run():
        p,_,_=configured()
        async def read(method):
            p.active_config=p.active_config.model_copy()
            return {'ok':True,'data':{'value':'old'}}
        p.delegate.read.side_effect=read
        result=await p.get_availability()
        assert result['error']['code']=='NOT_READY' and result['data'] is None
    asyncio.run(run())
