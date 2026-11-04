import pytest
from test_coordinator import system,event
from test_space import setup


@pytest.mark.asyncio
async def test_focus_blocks_before_model_and_after_generation(system):
    service,host,store=system
    host.multiplier=.3
    await service.cycle()
    assert not host.generated and not host.sent
    host.multiplier=1;host.version+=1;host.now+=1;host.events=[event(host.now,'new',host.version)]
    async def generate(*args,**kwargs):
        host.multiplier=.3
        return {'success':True,'response':'should not send'}
    host.generate=generate
    await service.cycle()
    assert not host.sent
    assert store.conn.execute("SELECT reason FROM actions WHERE event_id='new'").fetchone()[0]=='LIFE_FOCUSED_OR_RESTING'


@pytest.mark.asyncio
async def test_quiet_doubles_cooldown_without_backlog(system):
    service,host,store=system;host.multiplier=.6
    await service.cycle();assert len(host.sent)==1
    host.now+=service.settings.cooldown_seconds+1;host.version+=1
    host.events=[event(host.now,'too-soon',host.version)]
    await service.cycle();assert len(host.sent)==1
    host.now+=service.settings.cooldown_seconds;host.version+=1
    host.events=[event(host.now,'current',host.version)]
    await service.cycle();assert len(host.sent)==2
    assert len(store.conn.execute("SELECT * FROM actions WHERE event_id='too-soon' AND status='cancelled'").fetchall())==1


@pytest.mark.asyncio
async def test_space_requires_normal_participation_even_if_availability_free(tmp_path):
    service,db,port,_=setup(tmp_path)
    original=port.call
    async def quiet(name,**args):
        result=await original(name,**args)
        if name.endswith('get_public_life_context'):result['result']['data']['participation']['band']='quiet'
        return result
    port.call=quiet;port.material()
    try:
        await service.cycle();assert not port.calls and service.reason=='NOT_FREE'
    finally:db.close()
