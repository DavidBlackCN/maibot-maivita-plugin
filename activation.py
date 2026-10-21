"""Offline, revision-bound engine transitions. No implicit database migration."""
from pathlib import Path
import hashlib
import json
import os
from uuid import uuid4


def config_digest(config):
    return hashlib.sha256(json.dumps(config.model_dump(mode='json'), sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def read_record(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def atomic_record(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class EngineAuthority:
    def __init__(self, data):
        self.data = Path(data)
        self.path = self.data / 'engine-authority.json'
        self.ticket_path = self.data / 'engine-transition.json'

    def check(self, config):
        current = read_record(self.path)
        if current is None:
            return None
        if current.get('schema_version') != 1 or current.get('engine') not in ('life', 'legacy'):
            raise ValueError('unrecognized engine authority; refusing activation')
        if current.get('writer_lock') != str(Path(config.instance.writer_lock).resolve()):
            raise ValueError('authority writer_lock changed; refusing parallel ownership')
        if current['engine'] != config.engine.mode:
            ticket = read_record(self.ticket_path)
            if not ticket or any((ticket.get('schema_version') != 1,
                ticket.get('from_engine') != current['engine'],
                ticket.get('from_revision') != current['revision'],
                ticket.get('to_engine') != config.engine.mode,
                ticket.get('config_sha256') != config_digest(config))):
                raise ValueError('offline engine transition required; no automatic fallback')
            if not ticket.get('snapshots'):
                raise ValueError('transition requires verified rollback snapshots')
            for snapshot in ticket['snapshots']:
                path = self.data / snapshot['path']
                if (not path.resolve().is_relative_to(self.data.resolve()) or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != snapshot['sha256']):
                    raise ValueError('rollback snapshot missing or checksum mismatch')
        return current

    def activate(self, config):
        # Called only while the selected delegate owns the shared writer lease.
        current = self.check(config)
        changed = current is None or current['engine'] != config.engine.mode
        value = {'schema_version': 1, 'engine': config.engine.mode,
                 'revision': (current['revision'] if current else 0) + int(changed),
                 'config_sha256': config_digest(config),
                 'writer_lock': str(Path(config.instance.writer_lock).resolve()),
                 'database': config.engine.mode + ('/maivita.db' if config.engine.mode == 'life' else '/goals.db')}
        atomic_record(self.path, value)
        return value
