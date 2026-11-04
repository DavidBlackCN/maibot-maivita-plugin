"""Real Host/Runner/WebUI acceptance. Only model and QQ I/O are local substitutes."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from unittest.mock import patch
import tomli_w

VITA='davidblackcn.maibot-maivita-plugin'
DATE='github.xiexiaojia780.date-context-plugin'


def configure(work,configs,stream):
    config=configs['maibot-maivita-plugin']
    config['model'].update(daily_planning_enabled=False,generate_on_start=False,daily_budget=20)
    (work/'plugins/maibot-maivita-plugin/config.toml').write_text(tomli_w.dumps(config),encoding='utf8')
    (work/'plugins/date/config.toml').write_text(tomli_w.dumps({
        'plugin':{'enabled':True,'config_version':'1.4.5'},'date':{'timezone':'Asia/Shanghai'},
        'reply_injection':{'enabled':True},'planner_injection':{'enabled':True}}),encoding='utf8')


async def model_response(args,work):
    payload=json.loads(args['prompt'])
    with (work/'life-model.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(payload,ensure_ascii=False)+'\n')
    await asyncio.sleep(.15)
    if payload['task_kind']=='planning':
        now=datetime.fromisoformat(payload['as_of']).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai'))
        proposal={'kind':'apply_plan_patch','payload':{'local_date':now.date().isoformat(),
            'upserts':[{'item_id':'test-'+uuid.uuid4().hex,'name':'阅读参考安排','category':'rest','time_mode':'window',
                'window_start_at':(now+timedelta(minutes=1)).isoformat(),'window_end_at':(now+timedelta(hours=2)).isoformat(),
                'preferred_duration_minutes':45,'visibility':'public_summary'}],'remove_ids':[],'reason':'隔离模型安排'}}
    else:
        proposal={'event':'整理笔记','location':'书桌旁','emotion':'平静','minutes':45,'continue':False,'category':'study','sleep':False}
    return {'success':True,'response':json.dumps(proposal,ensure_ascii=False),'model_name':'local-fake',
            'usage':{'prompt_tokens':120,'completion_tokens':60,'total_tokens':180}}


async def run_cases(env):
    work=env['work'];supervisor=env['supervisor'];call=env['call'];cases=env['cases'];stream=env['stream']
    # This harness starts the real Supervisor directly, without the production bootstrap.
    env['manager']._started=True
    assert DATE in supervisor._registered_plugins
    assert await supervisor.reload_plugins([VITA],reason='providers-ready')
    from src.webui.app import create_app
    from src.webui.core.security import get_token_manager
    from src.common.runtime_loop import set_main_loop
    from src.chat.heart_flow.heartflow_manager import heartflow_manager
    from src.chat.message_receive.chat_manager import chat_manager
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from src.common.utils.utils_config import ChatConfigUtils
    from playwright.async_api import async_playwright
    import uvicorn
    import httpx
    import socket
    os.environ.pop('MAIBOT_WEBUI_USE_LOCAL_DASHBOARD',None)  # Official wheel 1.9.13.
    env['cfg'].webui.anti_crawler_mode='false'
    set_main_loop(asyncio.get_running_loop())
    app=create_app(host='127.0.0.1')
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1];url=f'http://127.0.0.1:{port}'
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off',timeout_graceful_shutdown=3))
    server_task=asyncio.create_task(server.serve(sockets=[sock]))
    async def until(predicate,seconds=35):
        async with asyncio.timeout(seconds):
            while not await predicate():await asyncio.sleep(.1)
    async def snapshot():return await call(VITA,'ui_snapshot')
    async def cooldown_done():
        runs=(await snapshot())['decisions']
        return not runs or time.time()-max(r['started_at_ms'] for r in runs)/1000>=61
    get_token_manager().update_token('Isolated-Test-'+uuid.uuid4().hex)
    get_token_manager().mark_setup_completed()
    token=get_token_manager().get_token()
    async with httpx.AsyncClient(base_url=url) as client:
        try:
            while not server.started:await asyncio.sleep(.05)
            root='/api/webui/plugins/runtime/webui/'+VITA
            response=await client.post(root+'/now/actions/manage',json={'args':{'action':'wake','request_id':'unauthenticated'}})
            assert response.status_code in (401,403),response.text
            client.cookies.set('maibot_session',token)
            response=await client.get('/api/webui/plugins/runtime/webui')
            assert response.status_code==200,response.text
            extension=next(e for e in response.json()['extensions'] if e['plugin_id']==VITA)
            assert {p['id'] for p in extension['pages']}=={'now','history','runtime'}
            cards=(await client.get('/api/webui/plugins/runtime/home-cards')).json()['cards']
            card=next(c for c in cards if c['plugin_id']==VITA)
            assert '加载时快照' in str(card['content']),card
            response=await client.post(root+'/now/actions/manage',json={'args':{'action':'wake','request_id':'unconfirmed'}})
            assert response.status_code in (400,409,422),response.text
            try:
                denied=await env['manager']._cap_api_call(env['PROBE'],'api.call',{'api_name':VITA+'.ui_action','args':{'action':'wake','request_id':'spoofed-admin'}})
            except (AttributeError,TypeError):
                raise
            except Exception:
                denied={'success':False}
            assert not denied.get('success'),denied
            cases.append('authenticated_webui_private_api_and_confirmation')
            before=await snapshot()
            for _ in range(3):assert (await snapshot())['state']['state_version']==before['state']['state_version']
            assert len((await snapshot())['usage'])==len(before['usage'])
            cases.append('snapshot_reads_do_not_advance_or_generate')
            date=await call(DATE,'date',{'timezone':'Asia/Shanghai','include_statutory_holidays':True})
            assert date.get('date')==before['calendar']['date'],date
            cases.append('original_date_plugin_native_api')
            for hook in ('maisaka.planner.before_request','maisaka.replyer.before_model_request'):
                result=await env['manager'].invoke_hook(hook,items=[],item_schema_version=1,session_id=stream,tool_definitions=[])
                assert not result.errors and not result.aborted,result
                assert before['calendar']['weekday'] in json.dumps(result.kwargs,ensure_ascii=False),result
            cases.append('date_injected_into_real_planner_replyer_request_hooks')
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(executable_path=os.environ['MAIVITA_BROWSER'],headless=True)
                context=await browser.new_context(viewport={'width':1280,'height':900})
                await context.route('**/*',lambda route:route.continue_() if route.request.url.startswith((url,'data:','blob:')) else route.abort())
                await context.add_cookies([{'name':'maibot_session','value':token,'url':url}])
                page=await context.new_page();errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(url+'/extensions/'+VITA+'/now')
                await page.screenshot(path=str(work/'initial.png'),full_page=True)
                frame=page.frame_locator('iframe')
                await frame.get_by_role('heading',name='此刻生活',exact=True).wait_for(timeout=25000)
                await frame.get_by_role('heading',name='管理生活').wait_for()
                assert not await frame.locator('#error').inner_text()
                await page.screenshot(path=str(work/'now.png'),full_page=True)
                await until(cooldown_done,160)
                await frame.get_by_role('button',name='重新决定当前活动',exact=True).click()
                await page.get_by_role('button',name='确认',exact=True).click()
                async def replanned():return ((await snapshot())['facts'].get('activity') or {}).get('name')=='整理笔记'
                await until(replanned)
                await frame.get_by_role('button',name='刷新',exact=True).click()
                await frame.get_by_text('整理笔记',exact=True).wait_for()
                await frame.get_by_role('button',name='请求睡眠',exact=True).click()
                await page.get_by_role('button',name='确认',exact=True).click()
                async def asleep():return (await snapshot())['facts']['sleep_phase']=='sleeping'
                await until(asleep)
                await frame.get_by_role('button',name='唤醒',exact=True).click()
                await page.get_by_role('button',name='确认',exact=True).click()
                async def awake():return (await snapshot())['facts']['awake']
                await until(awake)
                await frame.get_by_role('button',name='今日与最近',exact=True).click()
                await frame.get_by_role('heading',name='今日与最近',exact=True).wait_for()
                await until(cooldown_done,160)
                await frame.get_by_role('button',name='重新生成今日参考计划',exact=True).click()
                await page.get_by_role('button',name='确认',exact=True).click()
                async def planned():return bool((await snapshot())['plan']['items'])
                await until(planned)
                await frame.get_by_role('button',name='刷新',exact=True).click()
                await frame.get_by_text('阅读参考安排',exact=True).wait_for()
                await page.screenshot(path=str(work/'history.png'),full_page=True)
                await frame.get_by_role('button',name='设置与运行',exact=True).click()
                await frame.get_by_role('heading',name='设置与运行',exact=True).wait_for()
                await frame.get_by_role('heading',name='最近模型调用').wait_for()
                await page.set_viewport_size({'width':390,'height':844})
                assert await frame.locator('body').evaluate('(e)=>e.scrollWidth<=innerWidth+1')
                await page.screenshot(path=str(work/'mobile.png'),full_page=True)
                assert not errors,errors
                await browser.close()
            cases.append('official_dashboard_browser_three_pages_and_life_controls')
            prompts=[json.loads(line) for line in (work/'life-model.jsonl').read_text(encoding='utf8').splitlines()]
            assert {p['task_kind'] for p in prompts}>={'planning','segment'}
            assert all(p['context']['calendar']['date']==date['date'] for p in prompts)
            current=await snapshot()
            assert current['calendar']['calendar_status']=='available',current['calendar']
            assert current['usage'] and current['status']['used']>=len(current['usage'])
            cases.append('life_models_share_date_and_budget_usage')
            # Real runtime + official frequency capabilities, without starting network/model loops.
            runtime=MaisakaHeartFlowChatting(session_id=stream)
            heartflow_manager.heartflow_chat_list[stream]=runtime
            async def focused():return runtime._talk_frequency_adjust==.3
            await until(focused)
            with patch.object(runtime,'_is_focus_mode_active_for_current_chat',return_value=False),patch.object(ChatConfigUtils,'get_talk_value',return_value=.8):
                assert abs(runtime._get_effective_reply_frequency()-.24)<1e-9
                with patch.object(runtime,'_get_base_reply_frequency',return_value=0):assert runtime._get_effective_reply_frequency()==0
            with patch.object(runtime,'_is_focus_mode_active_for_current_chat',return_value=True):assert runtime._get_effective_reply_frequency()==1
            cases.append('native_frequency_changes_scheduler_input_and_focus_override')
            from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler
            scheduler=MessageTurnScheduler(runtime)
            runtime.message_cache=[env['raw']('ordinary')]
            with patch.object(runtime,'_get_message_quiet_period_remaining_seconds',return_value=0),patch.object(runtime._idle_backoff,'get_delay_seconds',return_value=0):
                result=scheduler._evaluate(1)
                assert '0.300' in result.detail,result
                runtime._arm_forced_turn_state(message_id='test-at',reason='@')
                assert '强制触发' in scheduler._evaluate(1).detail
                runtime._clear_forced_turn_state()
                runtime._agent_state=runtime._STATE_WAIT
                assert '快速反应' in scheduler._evaluate(1).detail
                runtime._agent_state=runtime._STATE_STOP
            private=await chat_manager.get_or_create_session('qq','10002',None,account_id='123456789',scope=None)
            private_runtime=MaisakaHeartFlowChatting(session_id=private.session_id)
            private_runtime.adjust_talk_frequency(.01)
            assert private_runtime._get_effective_reply_frequency()==1
            cases.append('actual_turn_gate_at_quick_response_and_private_chat')
            # Save through the official authenticated configuration API, then reload through Host.
            config_url='/api/webui/plugins/config/'+VITA
            response=await client.put(config_url,json={'config':{'availability':{'adjust_group_frequency':False}}})
            assert response.status_code==200,response.text
            assert await supervisor.reload_plugins([VITA],reason='official-ui-settings')
            assert runtime._talk_frequency_adjust==1
            assert (await snapshot())['frequency_enabled'] is False
            response=await client.put(config_url,json={'config':{'availability':{'adjust_group_frequency':True}}})
            assert response.status_code==200,response.text
            assert await supervisor.reload_plugins([VITA],reason='official-ui-settings')
            await until(focused)
            cases.append('official_config_save_disable_restore_reload_enable')
            assert (await supervisor.unload_plugins([VITA],reason='frequency-unload')).success
            assert runtime._talk_frequency_adjust==1
            # Prepare an observed quiet-body fixture while stopped; no live DB writes.
            import sys
            sys.path.insert(0,str(work/'plugins/maibot-maivita-plugin'))
            from maivita.life.storage.life_database import LifeDatabase
            from maivita.life.domain.clock import SystemClock
            from dataclasses import replace
            db=LifeDatabase(work/'data/plugins'/VITA/'life/maivita.db',clock=SystemClock())
            with db.unit_of_work() as u:
                state=u.states.get('default');activity=u.activities.get(state.primary_activity_id)
                # Activity rows are immutable: use the engine to switch category.
            from maivita.life.life_state.engine import LifeStateEngine
            from maivita.life.domain.commands import Command
            engine=LifeStateEngine(db)
            engine.execute(Command(kind='start_activity',payload={'name':'清醒休息','category':'rest','visibility':'public_summary'},source='legacy_observed',
                idempotency_key='quiet-fixture',expected_state_version=engine.state().state_version))
            with db.unit_of_work() as u:
                state=u.states.get('default');u.states.update(replace(state,state_version=state.state_version+1,energy=70,fatigue=65,sleepiness=50),expected_version=state.state_version);u.commit()
            assert await supervisor.reload_plugins([VITA],reason='quiet-fixture')
            async def quiet():return runtime._talk_frequency_adjust==.6
            await until(quiet)
            assert (await snapshot())['participation']['cooldown_factor']==2
            cases.append('quiet_multiplier_and_unload_restore_native')
            runtime.adjust_talk_frequency(.77)
            async def conflict():return '停止覆盖' in (await snapshot())['frequency']
            await until(conflict)
            assert runtime._talk_frequency_adjust==.77
            assert (await supervisor.unload_plugins([VITA],reason='conflict-test')).success
            assert runtime._talk_frequency_adjust==.77
            cases.append('observed_external_frequency_conflict_not_overwritten_on_unload')
        finally:
            env['manager']._started=False
            heartflow_manager.heartflow_chat_list.pop(stream,None)
            server.should_exit=True
            await server_task
