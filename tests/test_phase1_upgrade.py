"""Old schema/config copies upgrade without modifying originals; rollback is byte-exact."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
import r0_support
from test_life_engine import EngineFixture
from maibot_autonomous_planning_plugin.maivita.life.storage.schema import migrate

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parent/'maibot-maicoordinate-plugin'))
from tools.phase1_config import convert
from tools.phase1_data import upgrade
from tools.server_acceptance import run as server_check
from maicoordinate.store import Store
from maicoordinate.config import Settings


class PhaseOneUpgrade(EngineFixture,unittest.TestCase):
    def test_old_config_budget_and_space_modes(self):
        for enabled,expected in ((True,33),(False,17)):
            source={'plugin':{'config_version':'3'},'model':{'daily_budget':17,'task':'custom'},
                'scheduler':{'enabled':enabled,'daily_decision_budget':33},'instance':{'timezone':'UTC','allowed_streams':['stream']}}
            before=json.dumps(source)
            migrated,_=convert('vita',source)
            self.assertEqual(migrated['model']['daily_budget'],expected)
            self.assertEqual(migrated['instance']['allowed_streams'],['stream'])
            self.assertEqual(migrated['model']['task'],'custom')
            self.assertEqual(json.dumps(source),before)
            self.assertEqual(convert('vita',migrated)[0],migrated)
        self.assertEqual(convert('coordinate',{'mode':'controlled'})[0]['space']['mode'],'disabled')
        self.assertEqual(convert('coordinate',{},fresh=True)[0]['space']['mode'],'shadow')

    def test_schema6_and_coordinate1_copies_upgrade_and_restore(self):
        self.apply('advance',{'initialize_neutral':True})
        with closing(sqlite3.connect(self.db.path)) as c,c:
            c.execute('DROP TABLE body_rule_cutovers');c.execute('DELETE FROM schema_migrations WHERE version=7')
            before=c.execute('SELECT energy,fatigue,stress FROM life_state').fetchone()
        vita=self.db.path.parent
        coordinate=vita/'coordinate';coordinate.mkdir()
        store=Store(coordinate/'coordinate.sqlite3',Settings())
        with store.conn:
            store.conn.execute("INSERT INTO actions VALUES('vita','old-id','old-route','{}','digest','unknown','old unknown','{}','attempt',1)")
            store.conn.execute('DROP TABLE space_actions');store.conn.execute('PRAGMA user_version=1')
        store.close()
        with tempfile.TemporaryDirectory() as destination:
            output=Path(destination)/'upgrade'
            report=upgrade(vita,coordinate,output,stopped=True)
            self.assertTrue(report['source_unchanged']);self.assertTrue(report['rollback_copy_verified'])
            with closing(sqlite3.connect(output/'upgraded/vita/maivita.db')) as c:
                self.assertEqual(c.execute('SELECT energy,fatigue,stress FROM life_state').fetchone(),before)
                snapshot=list(c.iterdump());self.assertEqual(migrate(c,self.clock),7);self.assertEqual(list(c.iterdump()),snapshot)
            with closing(sqlite3.connect(output/'upgraded/coordinate/coordinate.sqlite3')) as c:
                self.assertEqual(c.execute("SELECT status FROM actions WHERE event_id='old-id'").fetchone()[0],'unknown')
                self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)


class ServerProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_uses_only_readonly_endpoints(self):
        from types import SimpleNamespace
        calls=[]
        class ExistingHost:
            async def invoke_api(self,plugin,name,args,timeout_ms):
                calls.append(name)
                return SimpleNamespace(error=None,payload={'success':True,'result':{'ready':True}})
        result=await server_check(ExistingHost())
        self.assertFalse(result['writes_requested'])
        self.assertEqual(calls,['get_public_life_context','get_status','get_space_contract'])
        with self.assertRaisesRegex(ValueError,'Explicit test target'):
            await server_check(ExistingHost(),execute=True)
