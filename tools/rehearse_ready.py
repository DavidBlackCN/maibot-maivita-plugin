"""Native life lifecycle and offline snapshot restore rehearsal; synthetic only."""
from contextlib import closing
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from tools.package import runtime_manifest
from maivita.life.storage.safety import WriterLease


def rehearse_restore(data, instance):
    """Restore verified snapshots to a disposable directory, never over live data."""
    data, instance = Path(data).resolve(), Path(instance).resolve()
    if not data.is_relative_to(instance):
        raise ValueError('data must belong to the isolated instance')
    authority_path = data / 'engine-authority.json'
    authority = json.loads(authority_path.read_text())
    if authority.get('schema_version') != 1 or authority.get('engine') != 'life' or authority.get('database') != 'life/maivita.db':
        raise ValueError('life authority required')
    lock_path = Path(authority['writer_lock'])
    if not lock_path.is_absolute() or not lock_path.resolve().is_relative_to(instance):
        raise ValueError('restore rehearsal lock must belong to the isolated instance')
    with WriterLease(lock_path):
        if json.loads(authority_path.read_text()) != authority:
            raise ValueError('authority changed before lock acquisition')
        if not (data / 'life/maivita.db').is_file():
            raise ValueError('life database missing')
        restored = []
        with tempfile.TemporaryDirectory(prefix='maivita-life-restore-', dir=instance) as temporary:
            temporary = Path(temporary)
            for name in ('maivita.db', 'management.sqlite3'):
                source = data / 'life' / name
                if not source.is_file():
                    continue
                if not source.resolve().is_relative_to(data):
                    raise ValueError('database must belong to the isolated data directory')
                snapshot = temporary / ('snapshot-' + name)
                target = temporary / ('restored-' + name)
                with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                    with closing(sqlite3.connect(snapshot)) as dst:
                        src.backup(dst)
                        if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                            raise ValueError('snapshot integrity failed')
                checksum = hashlib.sha256(snapshot.read_bytes()).hexdigest()
                shutil.copyfile(snapshot, target)
                if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                    raise ValueError('restore checksum mismatch')
                with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
                    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('restore integrity failed')
                    state = db.execute('SELECT state_version FROM life_state').fetchone()[0] if name == 'maivita.db' else None
                    events = db.execute('SELECT count(*) FROM life_events').fetchone()[0] if name == 'maivita.db' else None
                restored.append({'engine': 'life', 'database': name, 'sha256': checksum,
                                 'state_version': state, 'events': events})
        if json.loads(authority_path.read_text()) != authority:
            raise ValueError('authority changed during restore rehearsal')
    return {'rollback_snapshots': restored, 'authority_revision': authority['revision'],
            'restored_temporary_files_removed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', required=True, type=Path)
    args = parser.parse_args()
    instance = args.instance.resolve()
    if (not instance.is_relative_to(root / '.integration') or
            json.loads((instance / '.maivita-i1-test-instance.json').read_text())['production_data'] is not False):
        raise ValueError('workspace synthetic instance required')
    env = {**os.environ, 'MAIVITA_TEST_INSTANCE': str(instance)}
    subprocess.run([sys.executable, str(root / 'tools/run_i1_components.py')], cwd=root, env=env, check=True)
    # The native Host process has stopped before acquiring the shared writer lock.
    result = rehearse_restore(instance / 'data/plugins/davidblackcn.maibot-maivita-plugin', instance)
    result.update(passed=True, native_life_and_restore=True, single_writer=True,
                  runtime_files=runtime_manifest(), production_data=False, automatic_data_downgrade=False)
    path = root / 'evidence/integration-ready/restore.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'runtime_files'}, indent=2))


if __name__ == '__main__':
    main()
