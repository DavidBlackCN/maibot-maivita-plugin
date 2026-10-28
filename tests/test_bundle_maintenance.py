import importlib.util,json,sqlite3,unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bundle_maintenance',ROOT/'tools/product_bundle.py')
bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)

class MaintenanceTests(unittest.TestCase):
    def test_coherent_backup_restore_is_new_directory_only_and_checks_content(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);host=base/'host';plugin=host/'plugins/vita';plugin.mkdir(parents=True)
            (plugin/'_manifest.json').write_text(json.dumps({'id':bundle.IDS[0],'version':'test'}))
            (plugin/'config.toml').write_text('[plugin]\nenabled = true\n')
            data=host/'data/plugins'/bundle.IDS[0]/'life';data.mkdir(parents=True)
            with closing(sqlite3.connect(data/'maivita.db')) as db:db.execute('CREATE TABLE history(value TEXT)');db.execute("INSERT INTO history VALUES('preserved')");db.commit()
            backup=base/'backup';bundle.backup(host,backup,True)
            restored=base/'restore';bundle.restore(backup,restored)
            with closing(sqlite3.connect(restored/'data/plugins'/bundle.IDS[0]/'life/maivita.db')) as db:
                self.assertEqual(db.execute('SELECT value FROM history').fetchone()[0],'preserved')
            self.assertEqual((restored/'plugins/vita/config.toml').read_text(),(plugin/'config.toml').read_text())
            with self.assertRaises(ValueError):bundle.restore(backup,restored)
            with self.assertRaises(ValueError):bundle.backup(host,base/'forbidden',False)
            (backup/'contents/plugins/vita/config.toml').write_text('changed')
            with self.assertRaises(ValueError):bundle.restore(backup,base/'changed')
    def test_running_writer_lock_refuses_backup(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);host=base/'host';plugin=host/'plugins/hush';plugin.mkdir(parents=True)
            (plugin/'_manifest.json').write_text(json.dumps({'id':bundle.IDS[2],'version':'test'}))
            data=host/'data/plugins'/bundle.IDS[2];data.mkdir(parents=True)
            with bundle.lock(data/'writer.lock'):
                with self.assertRaises(OSError):bundle.backup(host,base/'backup',True)
            self.assertFalse((base/'backup').exists())
