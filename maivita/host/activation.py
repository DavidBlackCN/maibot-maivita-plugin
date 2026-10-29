"""Life engine ownership records. No implicit database migration."""
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

    def check(self, config):
        current = read_record(self.path)
        if current is None:
            return None
        if current.get('schema_version') != 1 or current.get('engine') != 'life':
            raise ValueError('unrecognized engine authority; refusing activation')
        if current.get('writer_lock') != str(Path(config.instance.writer_lock).resolve()):
            raise ValueError('authority writer_lock changed; refusing parallel ownership')
        return current

    def activate(self, config):
        # Called only while the selected delegate owns the shared writer lease.
        current = self.check(config)
        changed = current is None
        value = {'schema_version': 1, 'engine': 'life',
                 'revision': (current['revision'] if current else 0) + int(changed),
                 'config_sha256': config_digest(config),
                 'writer_lock': str(Path(config.instance.writer_lock).resolve()),
                 'database': 'life/maivita.db'}
        atomic_record(self.path, value)
        return value
