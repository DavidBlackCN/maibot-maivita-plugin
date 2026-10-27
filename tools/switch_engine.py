"""Prepare an offline isolated-instance switch; never edits live config or starts Host."""
from contextlib import closing
from pathlib import Path
import argparse
import hashlib
import json
import sqlite3
import sys
import types
from uuid import uuid4
import tomli_w

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('maivita_switch'); package.__path__ = [str(root)]
sys.modules.setdefault(package.__name__, package)
from maivita_switch.maivita.host.activation import EngineAuthority, atomic_record, config_digest, read_record
from maivita_switch.maivita.host.integration_config import IntegrationConfig
from maivita_switch.maivita.life.storage.safety import WriterLease


def prepare(config_path, data_dir, target, output):
    import tomllib
    config_path, data_dir, output = map(Path, (config_path, data_dir, output))
    if not all(path.is_absolute() for path in (config_path, data_dir, output)):
        raise ValueError('explicit absolute config, granted data directory and new output required')
    if output.exists(): raise ValueError('configuration output must not exist')
    cfg = IntegrationConfig.model_validate(tomllib.loads(config_path.read_text(encoding='utf-8')))
    cfg = cfg.resolved(data_dir)
    if target == cfg.engine.mode: raise ValueError('source and target engine must differ')
    source_mode = cfg.engine.mode
    cfg.engine.mode = target
    cfg.plugin.enabled = True  # Prepared output only; never starts a process.
    cfg = IntegrationConfig.model_validate(cfg.model_dump())
    authority = EngineAuthority(data_dir)
    with WriterLease(Path(cfg.instance.writer_lock)):
        active = read_record(authority.path)
        if not active or active['engine'] != source_mode: raise ValueError('no matching active source authority')
        if active.get('writer_lock') != str(Path(cfg.instance.writer_lock).resolve()):
            raise ValueError('writer_lock must match persisted authority')
        if not (data_dir / active['database']).is_file():
            raise ValueError('active source database missing; restore before switching')
        if read_record(authority.ticket_path) is not None:
            prior = read_record(authority.ticket_path)
            if prior.get('from_revision') == active['revision']:
                raise ValueError('unconsumed transition exists; inspect it before retrying')
        backups = []
        run_id = uuid4().hex
        for mode, filename in (('life', 'maivita.db'), ('legacy', 'goals.db')):
            source = data_dir / mode / filename
            if not source.exists(): continue
            destination = data_dir / 'engine-backups' / run_id / mode / filename
            destination.parent.mkdir(parents=True)
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                with closing(sqlite3.connect(destination)) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('backup integrity failed')
            backups.append({'engine': mode, 'path': destination.relative_to(data_dir).as_posix(),
                            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
        ticket = {'schema_version': 1, 'transition_id': run_id, 'from_engine': active['engine'],
                  'from_revision': active['revision'], 'to_engine': target,
                  'config_sha256': config_digest(cfg), 'snapshots': backups,
                  'production_data': False, 'automatic_activation': False}
        with output.open('xb') as stream: tomli_w.dump(cfg.model_dump(mode='json'), stream)
        atomic_record(authority.ticket_path, ticket)
        return ticket


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--target', choices=('life', 'legacy'), required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--isolated-instance', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config, args.data_dir, args.target, args.output), indent=2))


if __name__ == '__main__': main()
