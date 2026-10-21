"""Install immutable public Host source into the explicitly authorized empty path."""
from pathlib import Path,PurePosixPath
import hashlib
import io
import json
import tarfile
import sys
import argparse

ROOT=Path(__file__).resolve().parents[1]
COMMIT='5c7637e6913c84d1c3d5e742fa9e95a7e70d3501'
SHA='5c60746188d9050d972dee03a83a793a049f68f38be476393019ff824e0d70d7'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True,type=Path,help='explicit NEW empty source directory')
    TARGET=parser.parse_args().destination
    if not TARGET.is_absolute(): raise ValueError('absolute empty installation target required')
    if TARGET.resolve()!=TARGET.absolute(): raise ValueError('redirected installation target')
    if TARGET.exists() and any(TARGET.iterdir()): raise ValueError('nonempty target; refusing overwrite')
    raw=(ROOT/'.integration/cache'/(COMMIT+'.tar.gz')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA: raise ValueError('public archive checksum mismatch')
    records={}; skipped_links=[]
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        for member in archive.getmembers():
            parts=PurePosixPath(member.name).parts[1:]
            if not parts: continue
            relative=PurePosixPath(*parts)
            if relative.is_absolute() or '..' in parts or any(':' in p or '\\' in p for p in parts):
                raise ValueError('unsafe archive path')
            if not member.isfile():
                if member.isdir(): continue
                if relative.as_posix()=='CLAUDE.md' and member.issym() and member.linkname=='AGENTS.md':
                    skipped_links.append({'path':'CLAUDE.md','target':'AGENTS.md'}); continue
                raise ValueError('archive link or special member refused')
            if any(p in ('.git','data','logs','.env','.venv','config.toml','bot_config.toml','model_config.toml') for p in parts):
                continue
            records[relative.as_posix()]=archive.extractfile(member).read()
    TARGET.mkdir(parents=True,exist_ok=True)
    for relative,data in records.items():
        destination=TARGET/relative
        if destination.resolve()!=destination.absolute(): raise ValueError('redirected output')
        destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('xb') as stream: stream.write(data)
    lock={'host_commit':COMMIT,'archive_sha256':SHA,'skipped_document_aliases':skipped_links,
          'files':{relative:hashlib.sha256(data).hexdigest() for relative,data in records.items()}}
    (TARGET/'.maivita-i1-source.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
    print({'target':str(TARGET),'source_files':len(records),'host_started':False})


if __name__=='__main__': main()
