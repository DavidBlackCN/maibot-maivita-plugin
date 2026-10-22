"""Unified lifecycle and offline transition tests against real temporary SQLite."""
from pathlib import Path
from contextlib import closing
import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import tomli_w

import r0_support
from maibot_sdk.context import PluginContext, PluginPaths
from maibot_autonomous_planning_plugin.maivita.host.entrypoint import create_plugin
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
from maibot_autonomous_planning_plugin.maivita.life.storage.safety import WriterLease
from maibot_autonomous_planning_plugin.maivita.integrations.contracts import IdentityDecision, IdentityRequest, contract_descriptor

spec=importlib.util.spec_from_file_location('switch_fixture',r0_support.ROOT/'tools/switch_engine.py')
switch=importlib.util.module_from_spec(spec); spec.loader.exec_module(switch)


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='maivita-integration-')
        self.work=Path(self.temp.name).resolve(); self.calls=[]; self.instances=[]
        async def rpc(method,plugin_id,payload,**kwargs):
            self.calls.append(payload)
            cap=payload['capability']
            if cap=='component.list_loaded_plugins': return {'success':True,'plugins':[]}
            if cap=='config.get': return {'success':True,'value':{}}
            raise AssertionError('unexpected SDK side effect '+cap)
        self.ctx=PluginContext('davidblackcn.maibot-maivita-plugin',rpc_call=rpc,
                               paths=PluginPaths(self.work/'data',self.work/'runtime'))
        self.config={'plugin':{'enabled':True,'config_version':'3'},'engine':{'mode':'life'},
            'instance':{'acknowledgement':'isolated-test-instance','writer_lock':str(self.work/'writer.lock'),
                        'allowed_streams':['test-stream']},
            'legacy':{'planning':{'daily_enabled':False},'admin':{'legacy_admin_users':['test-admin']},
                      'compatibility':{'enable_proactive_trigger':False}}}

    def plugin(self,config=None):
        plugin=create_plugin(); plugin._set_context(self.ctx)
        plugin.set_plugin_config(config or self.config); plugin.get_components()
        self.instances.append(plugin); return plugin

    def save(self,plugin,name):
        path=self.work/name
        with path.open('wb') as stream: tomli_w.dump(plugin.config.model_dump(mode='json'),stream)
        return path

    async def asyncTearDown(self):
        for plugin in self.instances: await plugin.on_unload()
        self.temp.cleanup()

    async def test_formal_entry_uses_new_state_and_rejects_second_writer(self):
        first=self.plugin(); await first.on_load()
        self.assertTrue((await first.get_life_state())['ok'])
        self.assertTrue((self.work/'data/life/maivita.db').is_file())
        self.assertFalse((self.work/'data/legacy').exists())
        second=self.plugin()
        with self.assertRaisesRegex(RuntimeError,'another life writer'): await second.on_load()
        self.assertTrue((await first.get_engine_status())['ready'])
        descriptor=await first.get_integration_contract(owner=True)
        self.assertFalse(descriptor['owner_auth']['enabled'])

    async def test_sdk_default_discovery_before_config_does_not_freeze_disabled_delegate(self):
        plugin=create_plugin(); plugin.get_components()  # Real Host discovers metadata first.
        plugin._set_context(self.ctx); plugin.set_plugin_config(self.config); plugin.get_components()
        self.instances.append(plugin); await plugin.on_load()
        self.assertTrue((await plugin.get_life_state())['ok'])
        self.assertIsNotNone(plugin.delegate.deployment_lease.file)

    async def test_switch_and_roundtrip_require_offline_ticket_and_keep_both_databases(self):
        first=self.plugin(); await first.on_load(); config=self.save(first,'life.toml')
        with self.assertRaisesRegex(RuntimeError,'another life writer'):
            switch.prepare(config,self.work/'data','legacy',self.work/'blocked.toml')
        await first.on_unload()
        ticket=switch.prepare(config,self.work/'data','legacy',self.work/'legacy.toml')
        import tomllib
        legacy=self.plugin(tomllib.loads((self.work/'legacy.toml').read_text()))
        await legacy.on_load()
        self.assertEqual((await legacy.get_engine_status())['engine'],'legacy')
        self.assertIsNone(getattr(legacy.delegate,'runtime',None))
        self.assertEqual((await legacy.get_life_state())['error']['code'],'ENGINE_UNAVAILABLE')
        self.assertTrue((self.work/'data/legacy/goals.db').is_file())
        self.assertEqual(legacy.authority['revision'],2)
        self.assertEqual((await legacy.handle_generate_schedule(owner=True))['error']['code'],'PERMISSION_DENIED')
        denied=await legacy.handle_planning_command(text='/plan status',matched_groups={'planning_cmd':'/plan clear'})
        self.assertIn('trusted identity',denied[1])
        self.assertFalse(any(p['capability']=='frequency.set_adjust' for p in self.calls))
        for backup in ticket['snapshots']:
            path=self.work/'data'/backup['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),backup['sha256'])
            with closing(sqlite3.connect(path)) as db: self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        await legacy.on_unload()
        switch.prepare(self.work/'legacy.toml',self.work/'data','life',self.work/'back.toml')
        back=self.plugin(tomllib.loads((self.work/'back.toml').read_text())); await back.on_load()
        self.assertEqual(back.authority['revision'],3)
        self.assertTrue((await back.get_life_state())['ok'])
        self.assertTrue((self.work/'data/legacy/goals.db').exists())

    async def test_hot_switch_stops_writer_but_never_activates_other_engine(self):
        first=self.plugin(); await first.on_load()
        changed=first.config.model_dump(); changed['engine']['mode']='legacy'
        with self.assertRaisesRegex(RuntimeError,'reload'):
            await first.on_config_update('self',changed,'3')
        self.assertFalse(first.ready); self.assertFalse(first.delegate.runtime)
        bypass=self.plugin(changed)
        with self.assertRaisesRegex(ValueError,'offline engine transition'): await bypass.on_load()
        self.assertFalse((self.work/'data/legacy/goals.db').exists())

    async def test_failed_authority_commit_self_cleans_and_retains_lease_recoverability(self):
        plugin=self.plugin()
        with patch('maibot_autonomous_planning_plugin.maivita.host.activation.atomic_record',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): await plugin.on_load()
        self.assertFalse(plugin.ready)
        with WriterLease(self.work/'writer.lock'): pass
        self.assertFalse((self.work/'data/engine-authority.json').exists())
        self.assertFalse((await plugin.get_current_activity())['has_activity'])
        self.assertEqual((await plugin.silence_hook(message={'session_id':'test-stream'}))['action'],'abort')
        await plugin.on_load(); self.assertTrue(plugin.ready)

    async def test_changed_lock_and_unscoped_old_database_are_refused(self):
        plugin=self.plugin(); await plugin.on_load(); await plugin.on_unload()
        changed=plugin.config.model_dump(); changed['instance']['writer_lock']=str(self.work/'other.lock')
        other=self.plugin(changed)
        with self.assertRaisesRegex(ValueError,'writer_lock changed'): await other.on_load()
        (self.work/'data/goals.db').write_bytes(b'sentinel')
        with self.assertRaisesRegex(ValueError,'offline migration'): await plugin.on_load()
        self.assertEqual((self.work/'data/goals.db').read_bytes(),b'sentinel')

    async def test_tampered_target_config_cannot_consume_transition(self):
        first=self.plugin(); await first.on_load(); config=self.save(first,'life.toml'); await first.on_unload()
        switch.prepare(config,self.work/'data','legacy',self.work/'target.toml')
        changed=first.config.model_dump(); changed['engine']['mode']='legacy'; changed['model']['task_name']='changed'
        second=self.plugin(changed)
        with self.assertRaisesRegex(ValueError,'offline engine transition'): await second.on_load()
        self.assertEqual(json.loads((self.work/'data/engine-authority.json').read_text())['revision'],1)

    async def test_cancelled_durable_model_trigger_is_not_retried_on_restart(self):
        plugin=self.plugin(); await plugin.on_load(); db=plugin.delegate.runtime.database
        with db.unit_of_work() as u:
            u.connection.execute("INSERT INTO decision_triggers VALUES('cancelled-trigger','default','enqueue_replan',1,0)")
            u.commit()
        started=asyncio.Event(); calls=[]
        async def delayed(*args,**kwargs):
            calls.append('model'); started.set(); await asyncio.Event().wait()
        with patch.object(self.ctx.llm,'generate',side_effect=delayed):
            attempt=asyncio.create_task(plugin.delegate.runtime.process_triggers())
            await asyncio.wait_for(started.wait(),2)
            await plugin.on_unload(); await asyncio.gather(attempt,return_exceptions=True)
            with db.unit_of_work() as u:
                self.assertEqual(u.connection.execute('SELECT consumed FROM decision_triggers').fetchone()[0],1)
                self.assertEqual(u.connection.execute('SELECT status FROM decision_runs').fetchone()[0],'uncertain')
                # Simulate the old pre-fix on-disk cancellation gap as well.
                u.connection.execute('UPDATE decision_triggers SET consumed=0'); u.commit()
            await plugin.on_load()
            self.assertEqual((await plugin.delegate.runtime.process_triggers())['status'],'no_triggers')
            self.assertEqual(calls,['model'])


class ContractTests(unittest.TestCase):
    def test_frozen_shapes_never_authenticate_payload_or_accept_unknown_version(self):
        self.assertEqual(contract_descriptor()['contract_version'],'1.0.0')
        for field in ('owner_auth','policy','social','trusted_receipts','owned_frequency'):
            self.assertFalse(contract_descriptor()[field]['enabled'])
        for data in ({'owner':True},{'contract_version':'2.0.0','request_id':'r','context_ref':'c','action':'wake','stream_id':'s'}):
            with self.assertRaises(ValueError): IdentityRequest.model_validate(data)
        with self.assertRaises(ValueError):
            IdentityDecision.model_validate({'request_id':'r','verified':True,'subject_ref':None,
                'issuer':'untrusted','stream_id':'s','issued_at':'2026-10-21T00:00:00Z',
                'expires_at':'2026-10-21T00:01:00Z','proof_ref':None})
