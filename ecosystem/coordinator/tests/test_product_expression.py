import json
import pytest
from test_coordinator import system,event

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['life.activity.started','life.activity.ended','life.plan.revised'])
async def test_each_new_public_intent_uses_shared_persona_and_public_relationships(system,kind):
    service,host,store=system;host.events=[event()|{'event_type':kind}]
    prompts=[]
    async def generate(prompt,**kwargs):prompts.append(prompt);return {'success':True,'response':'简短生活表达'}
    host.generate=generate
    await service.cycle();assert len(host.sent)==1
    facts=json.loads(prompts[0].split('\n',1)[1]);assert facts['persona']=='shared persona'
    assert facts['public_group_relationships']['style']=='neutral'
    assert not facts['recent_sent_event_types']
    host.now+=service.settings.cooldown_seconds+1;host.version+=1
    host.events=[event(host.now,'next',host.version)]
    await service.cycle();assert len(host.sent)==2
    facts=json.loads(prompts[1].split('\n',1)[1]);assert facts['recent_sent_event_types']==[kind]

@pytest.mark.asyncio
@pytest.mark.parametrize('field',['persona_digest','social_revision'])
async def test_persona_or_relationship_changes_after_generation_cancel_send(system,field):
    service,host,store=system
    async def generate(*args,**kwargs):setattr(host,field,'b'*64);return {'success':True,'response':'不要发出'}
    host.generate=generate
    await service.cycle();assert not host.sent
    assert store.conn.execute('SELECT reason FROM actions').fetchone()[0]=='STATE_CHANGED'

@pytest.mark.asyncio
async def test_feature_negotiation_allows_patch_versions_but_requires_contract(system):
    service,host,store=system
    original=host.call
    async def changed(name,**kwargs):
        value=await original(name,**kwargs)
        if name.endswith('get_integration_contract'):value['result']['plugin_version']='0.5.99'
        return value
    host.call=changed;await service.cycle();assert len(host.sent)==1

@pytest.mark.asyncio
async def test_unknown_private_social_fields_never_reach_model(system):
    service,host,store=system;original=host.call
    async def changed(name,**kwargs):
        value=await original(name,**kwargs)
        if name.endswith('get_public_social_context'):value['result']['data']['private_accounts']=['secret']
        return value
    host.call=changed;await service.cycle()
    assert not host.sent and host.generated==0
