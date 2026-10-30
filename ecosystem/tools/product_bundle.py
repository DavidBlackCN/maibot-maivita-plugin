"""Offline bundle verification and coherent plugin-only backup/restore staging.

Python 3.12+. No network, model calls, Host patch, or in-place data replacement.
"""
from contextlib import ExitStack,contextmanager,closing
from pathlib import Path
import argparse,hashlib,json,os,shutil,sqlite3,tomllib,zipfile

IDS=('davidblackcn.maibot-maivita-plugin','davidblackcn.maibot-maiidentity-plugin',
     'davidblackcn.maibot-maihush-plugin','davidblackcn.maibot-maicoordinate-plugin','Rabbit-Jia-Er.MaiTrace')
PAIRS={('1.3.12','2.15.0'),('1.3.15','2.16.0'),('1.3.18','2.19.0')}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def files(root):
    for p in root.rglob('*'):
        if p.is_symlink() or p.is_junction():raise ValueError('Refusing linked backup path: '+str(p))
        if p.is_file() and not any(x in p.relative_to(root).parts for x in ('.git','__pycache__','.venv','logs','evidence')):yield p
@contextmanager
def lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    f=path.open('a+b')
    try:
        f.seek(0,2)
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:f.close()

def verify_bundle(bundle,host=None):
    bundle=bundle.resolve();manifest=json.loads((bundle/'bundle.json').read_text(encoding='utf8'))
    if host:
        import importlib.metadata
        version=tomllib.loads((host/'pyproject.toml').read_text(encoding='utf8'))['project']['version']
        sdk=importlib.metadata.version('maibot-plugin-sdk')
        if (version,sdk) not in PAIRS:raise ValueError(f'Unverified Host/SDK pair: {version}/{sdk}')
    found=[]
    for item in manifest['packages']:
        name=item['archive']
        if Path(name).name!=name:raise ValueError('Unsafe archive name')
        archive=bundle/name
        if sha(archive)!=item['sha256']:raise ValueError('Changed archive: '+name)
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Damaged ZIP')
            actual={n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}
            if actual!=item['files']:raise ValueError('Changed package entries')
            roots={n.split('/')[0] for n in z.namelist()}
            if len(roots)!=1:raise ValueError('Multiple package roots')
            for n in z.namelist():
                if '\\' in n or n.startswith('/') or '..' in Path(n).parts:raise ValueError('Unsafe ZIP entry')
            m=json.loads(z.read(next(iter(roots))+'/_manifest.json'))
            if m['id'] not in IDS or m['version']!=item['version']:raise ValueError('Manifest mismatch')
            found.append(m['id'])
    expected={IDS[0],IDS[3],IDS[4]} if manifest.get('phase')==1 else set(IDS[:4])
    if set(found)!=expected or len(found)!=len(expected):raise ValueError('Incomplete/duplicate bundle')
    return {'verified':True,'plugins':len(found),'host_checked':host is not None}

def backup(host,out,stopped):
    host=host.resolve();out=out.resolve()
    if not stopped:raise ValueError('Stop the complete Bot and pass --stopped')
    if out.exists() or out.is_relative_to(host):raise ValueError('Choose a new backup outside the Bot directory')
    roots=[];locks=set();found=set()
    for manifest in (host/'plugins').glob('*/_manifest.json'):
        meta=json.loads(manifest.read_text(encoding='utf8'));pid=meta.get('id')
        if pid not in IDS:continue
        if pid in found:raise ValueError('Duplicate installed plugin: '+pid)
        found.add(pid);root=manifest.parent
        if root.is_symlink() or root.is_junction():raise ValueError('Linked plugin directory')
        roots.append(root)
        cfg=tomllib.loads((root/'config.toml').read_text(encoding='utf8')) if (root/'config.toml').exists() else {}
        custom=cfg.get('instance' if 'maivita' in pid else 'plugin',{}).get('writer_lock')
        if custom:
            custom=Path(custom)
            if not custom.is_absolute():raise ValueError('Configured writer lock must be absolute')
            locks.add(custom.resolve())
        data=host/'data/plugins'/pid
        if data.exists():
            if data.is_symlink() or data.is_junction():raise ValueError('Linked plugin data directory')
            roots.append(data);locks.update(data.rglob('*.lock'))
        # Acquire default locks even on a fresh instance, closing startup races.
        locks.update(data/name for name in (('writer.lock','deployment-writer.lock','life/runtime-writer.lock') if 'maivita' in pid
             else ('writer.lock','deployment-writer.lock') if 'maiidentity' in pid
             else ('writer.lock',) if 'maihush' in pid else ('coordinate.lock',)))
    if not found:raise ValueError('No recognized plugins under this Host')
    with ExitStack() as stack:
        for p in sorted(locks):stack.enter_context(lock(p))
        out.mkdir(parents=True)
        hashes={}
        for root in roots:
            for source in files(root):
                if source.suffix=='.lock' or source.name.endswith(('-wal','-shm')):continue
                relative=source.relative_to(host);target=out/'contents'/relative;target.parent.mkdir(parents=True,exist_ok=True)
                if source.suffix in ('.db','.sqlite3'):
                    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
                else:shutil.copyfile(source,target)
                hashes[relative.as_posix()]=sha(target)
        write(out/'backup.json',{'schema':1,'plugins':sorted(found),'files':hashes,'complete':True})
    return {'backup':str(out),'files':len(hashes),'scope':'plugin code, config and owned data only'}

def restore(source,out):
    source=source.resolve();out=out.resolve()
    if out.exists() or out.is_relative_to(source):raise ValueError('Choose a new restore staging directory')
    meta=json.loads((source/'backup.json').read_text(encoding='utf8'))
    if meta.get('schema')!=1 or meta.get('complete') is not True:raise ValueError('Incomplete backup')
    for name,digest in meta['files'].items():
        p=(source/'contents'/name).resolve()
        if not p.is_relative_to(source/'contents') or sha(p)!=digest:raise ValueError('Unsafe or damaged backup')
        if Path(name).parts[0] not in ('plugins','data'):raise ValueError('Unexpected backup scope')
    out.mkdir(parents=True)
    for name in meta['files']:
        target=out/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/'contents'/name,target)
    return {'restore_staged':str(out),'instruction':'Stop Bot. Replace corresponding plugin code/config AND owned data together. Keep current data separately; never overlay old DB over current WAL.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    v=sub.add_parser('check');v.add_argument('--bundle',required=True,type=Path);v.add_argument('--host',type=Path)
    b=sub.add_parser('backup');b.add_argument('--host',required=True,type=Path);b.add_argument('--output',required=True,type=Path);b.add_argument('--stopped',action='store_true')
    r=sub.add_parser('restore');r.add_argument('--backup',required=True,type=Path);r.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    result=verify_bundle(a.bundle,a.host) if a.command=='check' else backup(a.host,a.output,a.stopped) if a.command=='backup' else restore(a.backup,a.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
