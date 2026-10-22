"""Native complete MainSystem lifecycle; local test operator, public components.

No Host modules are replaced. This supplements authenticated CLI/WebUI tests
with real Host component-query and Hook-dispatch invocation paths.
"""
from pathlib import Path
import asyncio
import json
import os
import sys


async def run():
    from src.common.runtime_loop import set_main_loop
    from src.common.shutdown import request_shutdown
    from src.runtime import raw_main,graceful_shutdown
    from src.plugin_runtime.component_query import component_query_service
    from src.plugin_runtime.integration import get_plugin_runtime_manager
    set_main_loop(asyncio.get_running_loop())
    system=raw_main(); scheduled=None
    results={}
    try:
        await system.initialize()
        scheduled=asyncio.create_task(system.schedule_tasks())
        await asyncio.sleep(.5)
        manager=get_plugin_runtime_manager()
        if os.environ.get('MAIVITA_EXPECT_ENGINE')=='legacy':
            supervisor=next(s for s in manager.supervisors if 'davidblackcn.maibot-maivita-plugin' in s._registered_plugins)
            response=await supervisor.invoke_plugin('plugin.invoke_api','davidblackcn.maibot-maivita-plugin',
                'get_engine_status',args={},timeout_ms=3000)
            status=response.payload['result']
            assert status['ready'] and status['engine']=='legacy',status
            assert component_query_service.get_tool_executor('maivita_status') is None
            denied=component_query_service.get_tool_executor('generate_schedule_v4')
            assert (await denied({'owner':True}))['error']['code']=='PERMISSION_DENIED'
            results['legacy_engine']={'passed':True,'status':status,'life_tool_absent':True,'mutations_denied':True}
            return results
        tool=component_query_service.get_tool_executor('maivita_status')
        assert tool is not None,'new Tool not registered'
        result=await tool({'view':'state','owner':True,'private_details':True})
        assert result['ok'] and result['api_version']==1,result
        results['tool']={'passed':True,'state_version':result['state_version'],'public_scope':True}
        denied=component_query_service.get_tool_executor('generate_schedule_v4')
        assert (await denied({'owner':True}))['error']['code']=='PERMISSION_DENIED'
        # Normal Host dispatcher, never a direct plugin Python method call.
        first=await manager.invoke_hook('maisaka.replyer.before_request',session_id='maivita-i1-synthetic-test-stream',extra_prompt='other-plugin')
        assert not first.aborted and '[MaiVita I1 state]' in first.kwargs['extra_prompt'],first
        second=await manager.invoke_hook('maisaka.replyer.before_request',**{k:v for k,v in first.kwargs.items() if k!='hook_name'})
        assert second.kwargs['extra_prompt'].count('[MaiVita I1 state]')==1
        assert 'other-plugin' in second.kwargs['extra_prompt']
        untouched=await manager.invoke_hook('send_service.before_send',message={'session_id':'outside-test-stream'})
        assert not untouched.aborted
        results['hook']={'passed':True,'dispatcher':'native complete Host','other_plugin_text_preserved':True,'deduplicated':True}
        # Exercise the registered Command through the normal native supervisor
        # RPC. No fake user identity or external message delivery is involved.
        supervisor=next(s for s in manager.supervisors if 'davidblackcn.maibot-maivita-plugin' in s._registered_plugins)
        response=await supervisor.invoke_plugin('plugin.invoke_command','davidblackcn.maibot-maivita-plugin','maivita',
            args={'text':'/maivita status','stream_id':'not-allowed-test-stream'},timeout_ms=3000)
        assert response.payload['success'] and response.payload['result'][0] is True,response
        results['command']={'passed':True,'unlisted_stream_denied':True,'platform_delivery':'not_tested'}
        from src.common.paths import get_instance_root
        # Real second writer refusal while the Host plugin owns its lease.
        from maivita_i1_source.maivita.life.storage.safety import WriterLease
        lease=WriterLease(get_instance_root()/'maivita-writer.lock')
        try: lease.acquire()
        except RuntimeError: results['single_writer']={'passed':True}
        else:
            lease.release(); raise AssertionError('second writer was accepted')
    finally:
        request_shutdown('i1_component_test_completed')
        if scheduled is not None:
            scheduled.cancel(); await asyncio.gather(scheduled,return_exceptions=True)
        results['shutdown']={'passed':await graceful_shutdown(system)}
        set_main_loop(None)
        target=Path(os.environ['MAIBOT_INSTANCE_DIR'])/'i1-components.json'
        target.write_text(json.dumps(results,indent=2),encoding='utf-8')
    return results


if __name__=='__main__':
    if sys.stdin.buffer.read(1)!=b'1': raise SystemExit('I1 job gate closed')
    # Import only MaiVita's lock primitive for a negative ownership check.
    import types
    package=types.ModuleType('maivita_i1_source')
    package.__path__=[os.environ['MAIVITA_I1_SOURCE']]
    sys.modules[package.__name__]=package
    # Original worker imports this module before creating its main loop; retain
    # that ordering because startup notices use their own short event loop.
    from src.runtime import raw_main,graceful_shutdown
    try:
        result=asyncio.run(asyncio.wait_for(run(),80))
        status=not all(value['passed'] for value in result.values())
    except BaseException:
        import traceback
        traceback.print_exc(); status=1
    from src.common.logger import shutdown_logging
    shutdown_logging()
    os._exit(int(status))
