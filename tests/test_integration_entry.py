"""Life lifecycle and ownership tests against real temporary SQLite."""
from pathlib import Path
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

import r0_support
from maibot_sdk.context import PluginContext, PluginPaths
from maibot_autonomous_planning_plugin.maivita.host.entrypoint import create_plugin
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
from maibot_autonomous_planning_plugin.maivita.life.storage.safety import WriterLease
from maibot_autonomous_planning_plugin.maivita.integrations.contracts import IdentityDecision, IdentityRequest, contract_descriptor

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
        self.config={'plugin':{'enabled':True,'config_version':'4'},
            'instance':{'writer_lock':str(self.work/'writer.lock'),
                        'allowed_streams':['test-stream']}}

    def plugin(self,config=None):
        plugin=create_plugin(); plugin._set_context(self.ctx)
        plugin.set_plugin_config(config or self.config); plugin.get_components()
        self.instances.append(plugin); return plugin

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

    async def test_default_install_lock_scope_sleep_and_reload(self):
        plugin=self.plugin({'plugin':{'config_version':'4'}})
        await plugin.on_load()
        self.assertTrue(plugin.ready)
        self.assertEqual(plugin.config.instance.writer_lock,'')
        self.assertEqual(plugin.authority['writer_lock'],str((self.work/'data/writer.lock').resolve()))
        self.assertTrue((await plugin.get_public_route_scope(stream_id='host-admitted-session'))['ok'])
        self.assertFalse((await plugin.get_public_route_scope(stream_id=''))['ok'])
        self.assertEqual(plugin.delegate.inject({'session_id':'host-admitted-session'})['action'],'continue')
        from types import SimpleNamespace
        with patch.object(plugin.delegate.runtime.engine,'state',return_value=SimpleNamespace(sleep_phase='sleeping')):
            self.assertEqual((await plugin.delegate.silence_hook(message={'session_id':'another-session'}))['action'],'abort')
        second=self.plugin({'plugin':{'config_version':'4'}})
        with self.assertRaisesRegex(RuntimeError,'another life writer'): await second.on_load()
        await plugin.on_config_update('self',plugin.config.model_dump(),2)
        self.assertTrue(plugin.ready)
        await plugin.on_unload(); await second.on_load()
        self.assertTrue(second.ready)

    async def test_explicit_stream_filter_still_excludes_other_sessions(self):
        plugin=self.plugin(); await plugin.on_load()
        self.assertFalse((await plugin.get_public_route_scope(stream_id='outside'))['ok'])
        self.assertEqual(plugin.delegate.inject({'session_id':'outside'})['action'],'continue')

    async def test_unsupported_host_refuses_before_storage(self):
        with patch.dict('os.environ',{'MAIBOT_HOST_VERSION':'1.3.14'}):
            plugin=self.plugin()
            with self.assertRaisesRegex(RuntimeError,'unsupported Host/SDK pair'):
                await plugin.on_load()
            self.assertFalse(plugin.ready)
            self.assertFalse((self.work/'data').exists())
            self.assertEqual(self.calls,[])

    async def test_sdk_default_discovery_before_config_does_not_freeze_disabled_delegate(self):
        plugin=create_plugin(); plugin.get_components()  # Real Host discovers metadata first.
        plugin._set_context(self.ctx); plugin.set_plugin_config(self.config); plugin.get_components()
        self.instances.append(plugin); await plugin.on_load()
        self.assertTrue((await plugin.get_life_state())['ok'])
        self.assertIsNotNone(plugin.delegate.deployment_lease.file)


    async def test_life_config_change_requires_reload_and_reuses_authority(self):
        first=self.plugin(); await first.on_load()
        changed=first.config.model_dump(); changed['model']['task']='replyer'
        with self.assertRaisesRegex(RuntimeError,'reload'):
            await first.on_config_update('self',changed,'4')
        self.assertFalse(first.ready)
        second=self.plugin(changed); await second.on_load()
        self.assertEqual(second.authority['engine'],'life')
        self.assertEqual(second.authority['revision'],1)
        self.assertEqual(second.authority['database'],'life/maivita.db')
        self.assertTrue((await second.get_life_state())['ok'])

    async def test_empty_writer_lock_inherits_persisted_authority(self):
        first=self.plugin(); await first.on_load(); await first.on_unload()
        changed=first.config.model_dump(); changed['instance']['writer_lock']=''
        second=self.plugin(changed); await second.on_load()
        self.assertEqual(second.authority['writer_lock'],str(self.work/'writer.lock'))
        self.assertFalse((self.work/'data/writer.lock').exists())

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

    async def test_retired_or_unknown_authority_cannot_activate_or_modify_data(self):
        first=self.plugin(); await first.on_load(); await first.on_unload()
        path=self.work/'data/engine-authority.json'
        db=self.work/'data/life/maivita.db'
        before=db.read_bytes()
        for engine in ('legacy','unknown'):
            record={**first.authority,'engine':engine}
            path.write_text(json.dumps(record))
            # An obsolete ticket cannot reactivate the retired engine.
            (self.work/'data/engine-transition.json').write_text(json.dumps({
                'schema_version':1,'from_engine':engine,'from_revision':1,'to_engine':'life'}))
            with self.subTest(engine=engine), self.assertRaisesRegex(ValueError,'unrecognized engine authority'):
                await self.plugin().on_load()
            self.assertEqual(json.loads(path.read_text()),record)
            self.assertEqual(db.read_bytes(),before)
        self.assertFalse((self.work/'data/legacy').exists())

    async def test_life_restore_rehearsal_requires_stop_and_preserves_live_files(self):
        from tools.rehearse_ready import rehearse_restore
        from maibot_autonomous_planning_plugin.maivita.host.management_requests import Requests
        first=self.plugin(); await first.on_load()
        first.delegate.management=Requests(self.work/'data/life/management.sqlite3')
        with self.assertRaisesRegex(RuntimeError,'another life writer'):
            rehearse_restore(self.work/'data',self.work)
        await first.on_unload()
        files=[self.work/'data/engine-authority.json', self.work/'data/life/maivita.db',
               self.work/'data/life/management.sqlite3']
        before={path:path.read_bytes() for path in files}
        report=rehearse_restore(self.work/'data',self.work)
        self.assertEqual({row['database'] for row in report['rollback_snapshots']},
                         {'maivita.db','management.sqlite3'})
        self.assertEqual(report['authority_revision'],1)
        self.assertEqual({path:path.read_bytes() for path in files},before)
        self.assertEqual(list(self.work.glob('maivita-life-restore-*')),[])
        self.assertTrue(report['restored_temporary_files_removed'])

    async def test_life_restore_rehearsal_rejects_corrupt_copy_and_retired_authority(self):
        from tools.rehearse_ready import rehearse_restore
        first=self.plugin(); await first.on_load(); await first.on_unload()
        def corrupt(source,target): Path(target).write_bytes(b'corrupt fixture')
        with patch('tools.rehearse_ready.shutil.copyfile',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'checksum'):
                rehearse_restore(self.work/'data',self.work)
        self.assertEqual(list(self.work.glob('maivita-life-restore-*')),[])
        with WriterLease(self.work/'writer.lock'): pass
        path=self.work/'data/engine-authority.json'
        path.write_text(json.dumps({**first.authority,'engine':'legacy'}))
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'life authority'):
            rehearse_restore(self.work/'data',self.work)
        self.assertEqual(path.read_bytes(),before)

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
            self.assertIn((await plugin.delegate.runtime.process_triggers())['status'],('no_triggers','cooldown'))
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
