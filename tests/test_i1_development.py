"""I1 adapter regressions. SDK transport/provider stand-ins are explicit."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import asyncio
import json
import tempfile
import unittest

import r0_support
from maibot_sdk.context import PluginContext,PluginPaths
from maibot_autonomous_planning_plugin.maivita.host.config import DevelopmentConfig
from maibot_autonomous_planning_plugin.maivita.host.model_port import SDKModelPort,parse_candidate
from maibot_autonomous_planning_plugin.maivita.host.plugin import MaiVitaDevelopmentPlugin,BEGIN
from maibot_autonomous_planning_plugin.maivita.life.domain.commands import Command
from maibot_autonomous_planning_plugin.maivita.life.storage.safety import WriterLease


def settings(work,**model):
    return {'plugin':{'enabled':True,'config_version':'2'},
        'instance':{'acknowledgement':'isolated-test-instance','writer_lock':str(work/'instance-writer.lock'),
                    'allowed_streams':['test-stream']},'model':{'daily_planning_enabled':False,**model}}


class DevelopmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='maivita-i1-unit-')
        self.work=Path(self.temp.name).resolve(); self.calls=[]; self.plugins=[]
        self.response={'success':True,'response':json.dumps({'kind':'winding_down','payload':{'reason':'test candidate'}}),'model_name':'test-provider-double'}
        async def rpc(method,plugin_id,payload,**kwargs):
            self.calls.append((method,payload))
            cap=payload['capability']
            if cap=='component.list_loaded_plugins': return {'success':True,'plugins':self.plugins}
            if cap=='llm.generate': return self.response
            if cap=='send.text': return {'success':True}
            raise AssertionError('unexpected capability '+cap)
        self.ctx=PluginContext('davidblackcn.maibot-maivita-plugin',rpc_call=rpc,
            paths=PluginPaths(self.work/'data',self.work/'runtime'))
        self.plugin=MaiVitaDevelopmentPlugin(); self.plugin._ctx=self.ctx
        self.plugin.set_plugin_config(settings(self.work))

    async def asyncTearDown(self):
        await self.plugin.on_unload(); self.temp.cleanup()

    async def test_new_lifecycle_reloads_and_never_imports_old_scheduler(self):
        await self.plugin.on_load()
        result=await self.plugin.runtime.request_replan('unit-explicit')
        self.assertEqual(result['status'],'applied')
        self.assertEqual((await self.plugin.get_life_state())['data']['sleep_phase'],'winding_down')
        runtime=self.plugin.runtime
        await self.plugin.on_unload(); self.assertFalse(runtime.opened)
        await self.plugin.on_load()
        self.assertEqual((await self.plugin.get_life_state())['data']['sleep_phase'],'winding_down')
        self.assertEqual([p.name for p in self.work.rglob('*.db')],['maivita.db'])

    async def test_failure_after_runtime_start_cleans_worker_and_both_leases(self):
        from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime
        original=DevelopmentRuntime.start
        def fail(runtime): original(runtime); raise RuntimeError('partial load failure')
        with patch.object(DevelopmentRuntime,'start',fail):
            with self.assertRaisesRegex(RuntimeError,'partial load'): await self.plugin.on_load()
        self.assertIsNone(self.plugin.runtime); self.assertIsNone(self.plugin.deployment_lease)
        lease=WriterLease(self.work/'instance-writer.lock'); lease.acquire(); lease.release()
        await self.plugin.on_load(); self.assertTrue(self.plugin.ready)

    async def test_old_plugin_and_legacy_data_are_refused_before_db_creation(self):
        self.plugins=['davidblackcn.maibot-autonomous-planning-plugin']
        with self.assertRaisesRegex(RuntimeError,'old full'): await self.plugin.on_load()
        self.assertFalse((self.work/'data/maivita.db').exists())
        self.plugins=[]; (self.work/'data').mkdir(); (self.work/'data/goals.db').write_bytes(b'legacy sentinel')
        with self.assertRaisesRegex(RuntimeError,'goals.db'): await self.plugin.on_load()
        self.assertEqual((self.work/'data/goals.db').read_bytes(),b'legacy sentinel')

    async def test_second_writer_refused_before_open_or_create(self):
        await self.plugin.on_load()
        other=MaiVitaDevelopmentPlugin(); other._ctx=self.ctx
        other.set_plugin_config(settings(self.work))
        with self.assertRaisesRegex(RuntimeError,'another life writer'): await other.on_load()
        self.assertTrue(self.plugin.ready); await other.on_unload()

    async def test_model_timeout_and_invalid_result_do_not_fabricate_facts(self):
        await self.plugin.on_load()
        before=self.plugin.runtime.engine.state()
        self.response={'success':True,'response':'not JSON'}
        result=await self.plugin.runtime.request_replan('bad')
        self.assertEqual(result['status'],'invalid')
        self.assertEqual(self.plugin.runtime.engine.state(),before)
        self.assertEqual(self.plugin.model_port.last['status'],'invalid_response')
        self.assertEqual(len([p for m,p in self.calls if p['capability']=='llm.generate']),2)
        self.assertTrue(result['degraded'])
        async def delayed(*args,**kwargs): await asyncio.sleep(2)
        self.plugin.model_port.settings.timeout_seconds=1
        with patch.object(self.ctx.llm,'generate',side_effect=delayed):
            with self.assertRaises(TimeoutError):
                await self.plugin.model_port(before,('timeout-test',))
        self.assertEqual(self.plugin.model_port.last['status'],'unavailable_unknown_outcome')
        self.assertEqual(self.plugin.runtime.engine.state(),before)

    async def test_privileged_APIs_tools_and_spoofed_owner_are_denied(self):
        await self.plugin.on_load()
        before=self.plugin.runtime.engine.state().state_version
        for method,args in [(self.plugin.request_replan,{'owner':True,'private_details':True}),
            (self.plugin.request_wakeup,{'owner':True}),(self.plugin.old_generate_tool,{'authorized':True}),
            (self.plugin.old_apply_tool,{}),(self.plugin.old_goal_tool,{'action':'delete'})]:
            with self.subTest(method=method.__name__): self.assertFalse((await method(**args))['ok'])
        self.assertEqual(self.plugin.runtime.engine.state().state_version,before)
        self.assertFalse(any('frequency' in p['capability'] for m,p in self.calls))

    async def test_read_commands_and_hooks_preserve_other_plugins_and_sleep_has_no_exception(self):
        await self.plugin.on_load()
        result=await self.plugin.command(message='/maivita status',stream_id='test-stream')
        self.assertTrue(result[0]); self.assertEqual(self.calls[-1][1]['capability'],'send.text')
        first=await self.plugin.replyer_hook(session_id='test-stream',extra_prompt='other-plugin',unknown={'keep':1})
        second=await self.plugin.replyer_hook(**first['modified_kwargs'])
        self.assertEqual(second['modified_kwargs']['extra_prompt'].count(BEGIN),1)
        self.assertIn('other-plugin',second['modified_kwargs']['extra_prompt'])
        self.assertEqual(second['modified_kwargs']['unknown'],{'keep':1})
        state=self.plugin.runtime.engine.state()
        self.plugin.runtime.engine.execute(Command(kind='start_sleep',idempotency_key='observed-sleep',
            expected_state_version=state.state_version,source='legacy_observed',payload={'duration_minutes':5}))
        before=sum(p['capability']=='send.text' for _,p in self.calls)
        await self.plugin.command(message='/maivita status',stream_id='test-stream',owner=True)
        self.assertEqual(sum(p['capability']=='send.text' for _,p in self.calls),before)
        hook=await self.plugin.silence_hook(message={'session_id':'test-stream','source_plugin_id':'trusted','owner':True})
        self.assertEqual(hook['action'],'abort')
        self.assertEqual((await self.plugin.silence_hook(message={'session_id':'other-stream'}))['action'],'continue')

    async def test_config_change_stops_writer_until_explicit_reload(self):
        await self.plugin.on_load(); update=settings(self.work,mode='model',model='new-model')
        with self.assertRaisesRegex(RuntimeError,'reload'): await self.plugin.on_config_update('self',update,'2')
        self.assertFalse(self.plugin.ready); self.assertIsNone(self.plugin.deployment_lease)
        self.assertEqual((await self.plugin.get_life_state())['error']['code'],'RELOAD_REQUIRED')

    async def test_SDK_model_route_is_explicit_and_never_uses_reasoning_as_facts(self):
        await self.plugin.on_load(); await self.plugin.runtime.request_replan('route')
        args=[p['args'] for m,p in self.calls if p['capability']=='llm.generate'][0]
        self.assertEqual(args['task_name'],'planner'); self.assertNotIn('model_name',args)
        prompt=json.loads(args['prompt']); self.assertIn('contract',prompt)
        for raw in ('{"kind":"winding_down","kind":"start_sleep","payload":{}}',
                    '{"kind":"winding_down","payload":{"reason":NaN}}','[]'):
            with self.subTest(raw=raw),self.assertRaises(ValueError): parse_candidate(raw)


class DevelopmentConfigTests(unittest.TestCase):
    def test_default_enabled_and_no_unsupported_feature_can_be_enabled(self):
        self.assertTrue(DevelopmentConfig().plugin.enabled)
        for section,value in [('instance',{'frequency_enabled':True}),('instance',{'owner_auth_enabled':True}),
                              ('instance',{'writer_lock':'relative.lock'}),('plugin',{'config_version':'1'})]:
            with self.subTest(value=value),self.assertRaises(ValueError): DevelopmentConfig.model_validate({section:value})

    def test_package_is_allowlisted_self_contained_and_not_released_without_acceptance(self):
        from maibot_autonomous_planning_plugin.tools.package import stage,build_zip,file_manifest
        with tempfile.TemporaryDirectory(prefix='maivita-i1-package-') as temp:
            directory=Path(temp).resolve(); target=directory/'candidate'
            manifest=stage(target)
            self.assertEqual(manifest,file_manifest())
            self.assertFalse(any(name.endswith(('config.toml','.db','.pyc','.env')) or name.startswith(('tests/','evidence/')) for name in manifest))
            self.assertIn('LICENSE',manifest); self.assertIn('ROLLBACK.md',manifest)
            self.assertFalse(any(name.startswith('maivita/legacy/') for name in manifest))
            # Internal notes must stay private even if an operator recreates
            # the ignored directory next to an otherwise installable package.
            import importlib
            packaging=importlib.import_module(stage.__module__)
            private=target/'docs/development'
            private.mkdir(parents=True)
            (private/'ARCHITECTURE.md').write_text('Internal packaging canary',encoding='utf-8')
            with patch.object(packaging,'ROOT',target):
                self.assertFalse(any(name.startswith('docs/development/') for name in packaging.entries()))
            import subprocess,sys
            code="import importlib.util,sys; p=sys.argv[1]; s=importlib.util.spec_from_file_location('i1_candidate',p+'/__init__.py',submodule_search_locations=[p]); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); from i1_candidate.plugin import create_plugin; assert create_plugin().get_components()"
            result=subprocess.run([sys.executable,'-c',code,str(target)],cwd=directory,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
            with self.assertRaisesRegex(ValueError,'incomplete'): build_zip(directory/'release.zip',{'passed':True})
            self.assertFalse((directory/'release.zip').exists())

    def test_default_install_package_does_not_accept_missing_or_stale_evidence(self):
        from maibot_autonomous_planning_plugin.tools.package import build_default_zip
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp).resolve()/'default.zip'
            for evidence in ({}, {'passed':True,'kind':'default_install','runtime_files':{}}):
                with self.assertRaisesRegex(ValueError,'incomplete'):
                    build_default_zip(target,evidence)
                self.assertFalse(target.exists())


class ModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = DevelopmentTests.asyncSetUp
    asyncTearDown = DevelopmentTests.asyncTearDown

    async def test_custom_routes_and_full_timeout_wiring(self):
        for mode, task, model, timeout in [('task','replyer','ignored',90),
                                         ('model','planner','custom-model',300)]:
            with self.subTest(mode=mode):
                self.plugin.set_plugin_config(settings(self.work,mode=mode,task=task,
                    model=model,timeout_seconds=timeout,max_tokens=16384))
                await self.plugin.on_load()
                runtime=self.plugin.runtime
                self.assertEqual(runtime.config.model_timeout_seconds,timeout)
                self.assertEqual(runtime.coordinator.timeout,timeout)
                # A real SDK call verifies payload serialization, not just Python kwargs.
                await self.plugin.model_port(runtime.engine.state(),('route-test',))
                args=[p['args'] for m,p in self.calls if p['capability']=='llm.generate'][-1]
                self.assertEqual(args['max_tokens'],16384)
                self.assertEqual(args['rpc_timeout_ms'],timeout*1000)
                self.assertEqual(args['task_name' if mode=='task' else 'model_name'],
                                 task if mode=='task' else model)
                self.assertNotIn('model_name' if mode=='task' else 'task_name',args)
                await self.plugin.on_unload()

    async def test_inner_timeout_records_failure_before_outer_cancellation(self):
        self.plugin.set_plugin_config(settings(self.work,timeout_seconds=1))
        await self.plugin.on_load()
        async def delayed(*args,**kwargs):
            await asyncio.sleep(2)
        with patch.object(self.ctx.llm,'generate',side_effect=delayed):
            result=await self.plugin.runtime.request_replan('nested-timeout')
        self.assertEqual(result['status'],'uncertain')
        self.assertEqual(self.plugin.model_port.last['status'],'unavailable_unknown_outcome')

    async def test_outer_wait_has_buffer_at_maximum_configuration(self):
        self.plugin.set_plugin_config(settings(self.work,timeout_seconds=300))
        await self.plugin.on_load()
        original=asyncio.wait_for
        timeouts=[]
        async def capture(awaitable, timeout):
            timeouts.append(timeout)
            return await original(awaitable,timeout)
        with patch('asyncio.wait_for',side_effect=capture):
            result=await self.plugin.runtime.request_replan('timeout-buffer')
        self.assertEqual(result['status'],'applied')
        self.assertIn(310,timeouts)
        self.assertIn(300,timeouts)
