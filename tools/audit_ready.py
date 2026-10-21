"""Audit public candidates and a clean export; secret values never leave memory."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from verify_ready import verification_manifest
from development.package import entries,runtime_manifest,HOST_COMMIT


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git','-c','safe.directory='+root.as_posix()]+list(args),cwd=root)


def main():
    inputs=verification_manifest()
    runtime_before=runtime_manifest()
    git('diff','--exit-code','--','.')
    names=sorted(set(git('ls-files','--cached','--others','--exclude-standard','-z').decode().strip('\0').split('\0')))
    names=[name for name in names if (root/name).is_file()]
    denied=[name for name in names if name.startswith(('.integration/','evidence/','dist/','.venv/')) or
            Path(name).name in ('config.toml','model_config.toml') or name.endswith(('.db','.sqlite','.pyc','.log','.pem','.key'))]
    assert not denied,denied
    secrets=[]
    for instance in ('i1-instance','ready-instance','ready-final-instance','ready-gated-instance'):
        directory=root/'.integration'/instance
        path=directory/'config/model_config.toml'
        if path.exists():
            config=tomllib.loads(path.read_text(encoding='utf-8'))
            secrets += [item.get('api_key','') for item in config.get('api_providers',[])
                        if len(item.get('api_key',''))>8 and item.get('api_key')!='your-api-key']
        auth=directory/'data/webui.json'
        if auth.exists(): secrets.append(json.loads(auth.read_text())['access_token'])
    content={name:(root/name).read_bytes() for name in names}
    indexed=set(git('ls-files','-z').decode().strip('\0').split('\0'))
    if indexed != set(names):
        raise ValueError('stage the complete public source before clean-export validation')
    index_content={name:git('show',':'+name) for name in names}
    content.update({'index/'+name:data for name,data in index_content.items()})
    content.update({'package/'+name:value for name,value in entries().items()})
    leaked=[name for name,data in content.items() if any(value.encode() in data for value in secrets if value)]
    generic=re.compile(rb'(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
    suspicious=[name for name,data in content.items() if generic.search(data)]
    assert not leaked and not suspicious,(leaked,suspicious)
    host=root.parent/'maibot'; lock=json.loads((host/'.maivita-i1-source.json').read_text())
    assert lock['host_commit']==HOST_COMMIT
    host_changes=[name for name,sha in lock['files'].items() if digest(host/name)!=sha]
    assert not host_changes,host_changes
    preserved=json.loads((root/'tests/fixtures/preserved-files.json').read_text())
    assert all(digest(root/name)==sha for name,sha in preserved.items())
    old=root.parent/'maibot-autonomous-planning-plugin'
    baseline=json.loads((root/'evidence/baseline.json').read_text())
    old_changed=[item['path'] for item in baseline['files'] if item.get('kind')!='original_design' and digest(old/item['path'])!=item['sha256']]
    owner=root.parent/'maibot-owner-auth-plugin'
    expected=json.loads((root/'evidence/r3-contract-matrix.json').read_text())['owner_auth']
    owner_changed=[name for name,sha in expected['source_files'].items() if digest(owner/name)!=sha]
    assert not old_changed and not owner_changed
    # Export only public source candidates; no private evidence is copied.
    with tempfile.TemporaryDirectory(prefix='ready-public-export-',dir=root/'.integration') as temporary:
        destination=Path(temporary).resolve()
        for name in names:
            target=destination/name
            assert target.resolve().is_relative_to(destination)
            target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(index_content[name])
        public_cache=destination/'.integration/cache'; public_cache.mkdir(parents=True)
        archive=root/'.integration/cache'/(HOST_COMMIT+'.tar.gz')
        shutil.copyfile(archive,public_cache/archive.name)
        commands=[['-m','pytest','tests','-q','-p','no:cacheprovider'],
                  ['tools/setup_i0_host.py','--offline'],
                  ['-m','unittest','integration_tests.test_environment','-v']]
        outcomes=[]
        env=dict(os.environ); env.pop('PYTHONPATH',None)
        for number,args in enumerate(commands):
            process=subprocess.run([sys.executable]+args,cwd=destination,env=env,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
            (root/'evidence/integration-ready'/('export-'+str(number)+'.txt')).write_bytes(process.stdout)
            outcomes.append(process.returncode==0)
            if process.returncode: raise RuntimeError('clean public export check failed; see export-'+str(number)+'.txt')
    if inputs!=verification_manifest() or runtime_before!=runtime_manifest():
        raise ValueError('source changed during export checks; no audit acceptance')
    git('diff','--exit-code','--','.')
    result={'passed':True,'public_files':len(names),'private_paths':denied,'credential_leak_paths':leaked,
        'generic_secret_findings':suspicious,'host_source_files_verified':len(lock['files']),
        'host_source_changed':host_changes,'original_plugin_changed':old_changed,'owner_auth_changed':owner_changed,
        'public_export_pytest_runner_and_cleanup':all(outcomes),'temporary_export_removed':True,
        'runtime_files':runtime_before,'verification_files':inputs,'production_data':False}
    (root/'evidence/integration-ready/source-audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key not in ('runtime_files','verification_files')},indent=2))


if __name__=='__main__': main()
