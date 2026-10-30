"""Execute patched upstream modules; replace model/platform IO only."""
import asyncio
import importlib
import os
from pathlib import Path
import sys
import types
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest

SOURCE=Path(os.environ.get('MAITRACE_SOURCE',Path(__file__).resolve().parents[4]/'.implementation/vendor/MaiTrace'))
pkg=types.ModuleType('phase1_trace');pkg.__path__=[str(SOURCE)];sys.modules['phase1_trace']=pkg
config=importlib.import_module('phase1_trace.config')
apis=importlib.import_module('phase1_trace.handlers.apis')
publish=importlib.import_module('phase1_trace.services.feed_publish')
monitor=importlib.import_module('phase1_trace.services.monitor')
contract=importlib.import_module('phase1_trace.services.space_contract')


def fixture():
    cfg=config.MaiTracePluginConfig.model_validate({'life':{'enabled':False},'schedule':{'enabled':False},
        'messaging':{'enabled':False},'send':{'enable_chat_memory':False},
        'routine':{'post_enabled':False,'browse_enabled':False},
        'monitor':{'read_list_type':'whitelist','read_list':['12345'],'enable_auto_reply':True,'comment_probability':1.,'like_probability':1.}})
    now=datetime.now(timezone.utc)
    activity={'activity_id':'life-1','name':'读书','category':'rest','location':'家','emotion':'平静',
        'visibility':'public_summary','started_at':(now-timedelta(minutes=20)).isoformat(),'ended_at':None,
        'duration_minutes':20,'continuity_unknown':False}
    life={'awake':True,'as_of':now.isoformat(),'availability':{'state':'free'},'activity':activity,'history':[activity]}
    async def call(*args,**kwargs): return {'success':True,'result':{'ok':True,'data':life}}
    persistence=NS(load_processed_list=AsyncMock(return_value={}),load_processed_comments=AsyncMock(return_value={}),
        save_processed_list=AsyncMock(),save_processed_comments=AsyncMock())
    plugin=NS(config=cfg,ctx=NS(call_host_method=call,db=NS(get=AsyncMock(return_value=[]))),
        schedule_engine=None,life_engine=None,persistence=persistence)
    return plugin,life,{'activity':activity,'current':True}


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['none','sleep','activity','image_failure','unknown','wrong_account','record_failure'])
async def test_publication_generation_final_guard_and_unknown(monkeypatch,change):
    plugin,life,basis=fixture();submitted=[]
    async def generate(*args,**kwargs):
        if change=='sleep':life['awake']=False
        if change=='activity':life['activity']={**life['activity'],'activity_id':'changed'}
        return True,'安静地读了一会儿书。'
    async def submit(story,images,publication):
        submitted.append(story);publication.stage='submitting'
        if change=='unknown':raise publish.PublicationUncertainError('unknown')
        publication.tid='tid-1';publication.stage='confirmed';return 'tid-1'
    monkeypatch.setattr(publish,'create_qzone_publisher',AsyncMock(return_value=NS(uin='99999',publish_emotion=submit)))
    monkeypatch.setattr(publish,'resolve_persona',AsyncMock(return_value=NS(personality='quiet',style='plain',self_description='',reference_image_path='')))
    monkeypatch.setattr(publish,'build_send_prompt',AsyncMock(return_value='public prompt'))
    monkeypatch.setattr(publish.LLMRunner,'generate',generate)
    monkeypatch.setattr(publish,'collect_images_for_feed',AsyncMock(side_effect=publish.ImageGenerationError('unconfigured')) if change=='image_failure' else AsyncMock(return_value=[]))
    monkeypatch.setattr(publish,'record_activity',AsyncMock(side_effect=OSError('disk full')) if change=='record_failure' else AsyncMock())
    response=await apis.publish_topic_api(plugin,life_basis=basis,expected_account='10000' if change=='wrong_account' else '')
    if change in ('sleep','activity'):
        assert response['status']=='deferred' and not submitted
    elif change=='wrong_account':assert response['status']=='failed' and not submitted
    elif change=='unknown':assert response['status']=='unknown' and len(submitted)==1
    else:assert response['status']=='success' and response['tid']=='tid-1' and len(submitted)==1


@pytest.mark.asyncio
async def test_direct_publication_record_failure_preserves_success(monkeypatch):
    plugin,_,_=fixture()
    monkeypatch.setattr(apis,'create_qzone_publisher',AsyncMock(return_value=NS(uin='99999',publish_emotion=AsyncMock(return_value='tid-known'))))
    monkeypatch.setattr(apis,'record_activity',AsyncMock(side_effect=OSError('disk full')))
    result=await apis.send_feed_api(plugin,message='public text')
    assert result['status']=='success' and result['tid']=='tid-known'


@pytest.mark.asyncio
async def test_browse_like_comment_reply_whitelist_and_round_bound(monkeypatch):
    plugin,_,_=fixture()
    feeds=[{'tid':'friend','target_qq':'12345','content':'hi'},
           {'tid':'self','target_qq':'99999','content':'mine','comments':[
               {'qq_account':'12345','comment_tid':'allowed','content':'hello'},
               {'qq_account':'88888','comment_tid':'blocked','content':'secret'}]},
           {'tid':'outsider','target_qq':'88888','content':'skip'}]
    qzone=NS(uin='99999',monitor_get_list=AsyncMock(return_value=feeds),like=AsyncMock(return_value=True),
        comment=AsyncMock(return_value=True),reply=AsyncMock(return_value=True))
    monkeypatch.setattr(monitor,'make_qzone_api',AsyncMock(return_value=qzone))
    monkeypatch.setattr(monitor,'resolve_persona',AsyncMock(return_value=NS(personality='',style='',self_description='')))
    monkeypatch.setattr(monitor,'is_in_silent_period',lambda *a,**kw:(False,True,True))
    monkeypatch.setattr(monitor.LLMRunner,'generate',AsyncMock(return_value=(True,'hello')))
    monkeypatch.setattr(monitor,'record_activity',AsyncMock())
    for name in ('build_comment_prompt','build_reply_prompt','build_reply_to_reply_prompt'):
        monkeypatch.setattr(monitor,name,lambda *a,**kw:'public interaction')
    monkeypatch.setattr(monitor.asyncio,'sleep',AsyncMock())
    result=await contract.browse_api(plugin,5,['12345'])
    assert result['status']=='success' and result['round_result_only'] is True
    assert qzone.like.await_count==1 and qzone.comment.await_count==1 and qzone.reply.await_count==1
    assert qzone.reply.call_args.args[4]=='allowed'
    result=await contract.browse_api(plugin,5,[])
    assert result['status']=='deferred' and qzone.like.await_count==1


@pytest.mark.asyncio
async def test_browse_shared_lock_rejects_parallel_round(monkeypatch):
    plugin,_,_=fixture();lock=contract.browse_lock(plugin)
    async with lock:
        result=await contract.browse_api(plugin,5,['12345'])
        assert result['status']=='deferred' and result['message']=='BROWSE_BUSY'
        assert plugin.persistence.load_processed_list.await_count==0


def test_readonly_contract_and_native_sdk_registration():
    plugin,_,_=fixture()
    assert contract.contract(plugin)['ready']
    assert contract.contract(plugin)['budgets']['browse']>2
    entry=importlib.import_module('phase1_trace.plugin')
    actual=entry.MaiTracePlugin()
    actual.set_plugin_config(plugin.config.model_dump())
    names={c['name'] for c in actual.get_components()}
    assert {'get_space_contract','browse_once_api','publish_topic_api'}<=names
