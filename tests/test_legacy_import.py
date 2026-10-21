from contextlib import closing
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest

from test_life_engine import EngineFixture
from r1_support import AT,ROOT
from maibot_autonomous_planning_plugin.domain.models import DomainError
from maibot_autonomous_planning_plugin.storage.legacy_import import import_snapshot
from maibot_autonomous_planning_plugin.storage.schema import migrate,migrations,validate_schema
from maibot_autonomous_planning_plugin.storage.life_database import LifeDatabase,connect


class ImportTests(EngineFixture,unittest.TestCase):
    def source(self):
        path=Path(self.temp.name).resolve()/'snapshot.db'
        columns=['goal_id','name','description','goal_type','priority','creator_id','chat_id','status','created_at','deadline','interval_seconds','conditions','parameters','progress','last_executed_at','execution_count','updated_at']
        with closing(sqlite3.connect(path)) as c:
            c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)'); c.execute("INSERT INTO metadata VALUES('schema_version','1')")
            c.execute('CREATE TABLE goals('+','.join(name+' TEXT'+(' PRIMARY KEY' if name=='goal_id' else '') for name in columns)+')')
            rows=[('future-active','pending_commitment','active',{'commitment_date':'2099-01-01'}),
                  ('future-consumed','pending_commitment','completed',{'commitment_date':'2099-01-02'}),
                  ('cross-midnight','study','completed',{'time_window':[1380,1500]}),
                  ('unknown','unrecognized','active',{}),('bad','study','active','{broken')]
            for key,kind,status,p in rows:
                data=dict.fromkeys(columns)
                data.update(goal_id=key,name=key,description='',goal_type=kind,priority='high',creator_id='synthetic',chat_id='private',status=status,created_at=AT.isoformat(),conditions='{}',parameters=p if isinstance(p,str) else json.dumps(p),progress='0',execution_count='0')
                c.execute('INSERT INTO goals VALUES('+','.join('?' for _ in columns)+')',[data[k] for k in columns])
            c.commit()
        return path

    def test_T12_T13_conservation_protection_bad_json_and_rerun(self):
        source=self.source(); before=source.read_bytes()
        result=import_snapshot(self.db,source,source_database_id='fixture-id')
        self.assertEqual(result['source_rows'],5); self.assertFalse(result['activated'])
        self.assertEqual(sum(result['counts'].values()),5); self.assertEqual(len(result['review_ids']),3)
        rerun=import_snapshot(self.db,source,source_database_id='fixture-id'); self.assertEqual(rerun['reused'],5)
        with self.db.unit_of_work() as u:
            self.assertEqual(u.activities.all(),[]); self.assertEqual(u.sessions.all(),[])
            self.assertEqual({c.status for c in u.commitments.all()},{'confirmed','needs_review'})
            self.assertTrue(all(c.fulfillment_evidence is None and c.start_at is None for c in u.commitments.all()))
            item=u.items.all()[0]; self.assertEqual(item.status,'superseded')
            self.assertEqual((item.end_at-item.start_at).total_seconds(),7200)
            self.assertEqual(u.connection.execute('SELECT count(*) FROM legacy_import_records').fetchone()[0],5)
        self.assertEqual(source.read_bytes(),before)

    def test_T13_changed_source_row_is_explicit_conflict_and_all_new_rows_rollback(self):
        source=self.source(); import_snapshot(self.db,source,source_database_id='fixture-id')
        with closing(sqlite3.connect(source)) as c:
            c.execute("UPDATE goals SET name='changed' WHERE goal_id='future-active'"); c.commit()
        with self.assertRaisesRegex(DomainError,'changed'): import_snapshot(self.db,source,source_database_id='fixture-id')
        with self.db.unit_of_work() as u: self.assertEqual(u.connection.execute('SELECT count(*) FROM migration_runs').fetchone()[0],1)

    def test_T13_import_failure_rolls_back_run_rows_and_entities(self):
        source=self.source()
        def fail(point): raise OSError('injected interruption')
        with self.assertRaises(OSError): import_snapshot(self.db,source,source_database_id='fixture-id',fault=fail)
        with self.db.unit_of_work() as u:
            self.assertEqual(u.commitments.all(),[])
            self.assertEqual(u.connection.execute('SELECT count(*) FROM legacy_import_records').fetchone()[0],0)
        self.assertEqual(import_snapshot(self.db,source,source_database_id='fixture-id')['source_rows'],5)

    def test_explicit_schema1_upgrade_retains_actuals_immutable_migration_and_checks(self):
        old=Path(self.temp.name).resolve()/'schema1.db'; old.touch()
        with closing(connect(old)) as c:
            migrate(c,self.clock,migrations()[:1])
            c.execute("INSERT INTO migration_runs VALUES('r','synthetic','hash','discovered',1,NULL,'{}')")
            self.assertEqual(validate_schema(c,migrations()[:1]),1)
            with self.assertRaisesRegex(Exception,'migration required'): LifeDatabase(old)
            migrate(c,self.clock)
            self.assertEqual(validate_schema(c),4)
            self.assertEqual(c.execute('SELECT migration_run_id FROM migration_runs').fetchone()[0],'r')
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0],1)
        archived=json.loads((ROOT/'tests/fixtures/preserved-files.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256((ROOT/'storage/migrations/001_initial.sql').read_bytes()).hexdigest(),archived['storage/migrations/001_initial.sql'])

    def test_populated_circular_foreign_keys_survive_table_rebuild(self):
        from r1_support import state,activity
        from maibot_autonomous_planning_plugin.storage.repositories import SPECS
        path=Path(self.temp.name).resolve()/'populated-schema1.db'; path.touch()
        with closing(connect(path)) as c:
            migrate(c,self.clock,migrations()[:1])
            c.execute('BEGIN')
            for name,record in [('states',state(primary_activity_id='a1')),('activities',activity())]:
                spec=SPECS[name]; values=spec.encode(record)
                c.execute(f'INSERT INTO {spec.table} ({",".join(spec.columns)}) VALUES ({",".join("?" for _ in values)})',values)
            c.commit()
            migrate(c,self.clock)
            self.assertEqual(validate_schema(c),4)
            self.assertEqual(c.execute('SELECT primary_activity_id FROM life_state').fetchone()[0],'a1')
            self.assertEqual(c.execute('SELECT name FROM activities').fetchone()[0],'Observed study')
            self.assertEqual(list(c.execute('PRAGMA foreign_key_check')),[])
