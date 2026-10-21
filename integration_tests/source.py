"""Pinned public Host fixture, immutable source verification and safe setup."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '5c7637e6913c84d1c3d5e742fa9e95a7e70d3501'
ARCHIVE_SHA256 = '5c60746188d9050d972dee03a83a793a049f68f38be476393019ff824e0d70d7'
HOST = ROOT / '.integration' / 'host' / COMMIT
LOCK = ROOT / 'tests/fixtures/i0-source-lock.json'


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locked_files():
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    if lock['commit'] != COMMIT or lock['archive_sha256'] != ARCHIVE_SHA256:
        raise ValueError('I0 Host source lock changed; review required')
    result = {}
    for item in lock['extracted_files']:
        relative = PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in item['path'] or ':' in item['path']:
            raise ValueError('unsafe Host fixture path')
        result[relative.as_posix()] = item['sha256']
    return result


def verify_source():
    files = locked_files()
    for relative, expected in files.items():
        path = HOST / relative
        if (path.resolve()!=path.absolute() or not path.is_file() or path.is_symlink() or file_digest(path) != expected):
            raise ValueError('missing/changed I0 Host source: ' + relative)
    unexpected={p.relative_to(HOST).as_posix() for p in HOST.rglob('*') if p.is_file()}-files.keys()
    if unexpected:
        raise ValueError('unexpected files in immutable Host fixture: '+str(sorted(unexpected)))
    return len(files)


def setup_source(*, offline=False):
    files = locked_files()
    archive_path = ROOT / '.integration' / 'cache' / (COMMIT + '.tar.gz')
    if not archive_path.exists():
        if offline:
            raise FileNotFoundError('I0 source cache absent; run setup_i0_host.py once online')
        url = 'https://codeload.github.com/Mai-with-u/MaiBot/tar.gz/' + COMMIT
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(20_000_001)
        if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Host archive size/checksum mismatch')
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open('xb') as stream:
            stream.write(data)
    if file_digest(archive_path) != ARCHIVE_SHA256:
        raise ValueError('I0 cached source checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(archive_path.read_bytes()), mode='r:gz') as archive:
        for member in archive.getmembers():
            relative = '/'.join(PurePosixPath(member.name).parts[1:])
            if relative not in files:
                continue
            if not member.isfile() or member.issym() or member.islnk():
                raise ValueError('non-regular Host source member')
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != files[relative]:
                raise ValueError('Host source content mismatch')
            path = HOST / relative
            if path.resolve()!=path.absolute():
                raise ValueError('refusing redirected Host fixture path: '+relative)
            if path.exists():
                if file_digest(path) != files[relative]:
                    raise ValueError('refusing to overwrite changed Host fixture: ' + relative)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(data)
    return verify_source()
