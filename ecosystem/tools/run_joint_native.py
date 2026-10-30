"""Stage four real plugins with a pinned Host; deny all non-loopback network."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import signal
import tempfile
import zipfile
import maibot_sdk

ROOT=Path(__file__).resolve().parents[1]
HOST=ROOT/'maibot-maiIdentity-plugin/.integration/host'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--host-source',type=Path,default=HOST)
    parser.add_argument('--source-lock',type=Path,default=ROOT/'maibot-maihush-plugin/tests/fixtures/source-lock.json')
    parser.add_argument('--auto-routes',action='store_true')
    parser.add_argument('--trusted-management',action='store_true')
    parser.add_argument('--space',action='store_true')
    parser.add_argument('--maitrace-source',type=Path)
    parser.add_argument('--server-probe-source',type=Path)
    args=parser.parse_args()
    host=args.host_source.resolve()
    verification={name:digest(ROOT/'tools'/name) for name in ('run_joint_native.py','joint_native_scenario.py')}
    work=args.output.resolve()
    if work.exists(): raise ValueError('new isolated directory required')
    lock=json.loads(args.source_lock.read_text())
    for name,sha in lock['host_files'].items():
        if digest(host/name)!=sha: raise ValueError('Host source mismatch: '+name)
    site=Path(maibot_sdk.__file__).parent.parent
    sdk=importlib.metadata.version('maibot-plugin-sdk')
    host_version={'2.15.0':'1.3.12','2.16.0':'1.3.15','2.19.0':'1.3.18'}[sdk]
    for name,sha in lock['sdk_files'].items():
        if digest(site/name)!=sha: raise ValueError('SDK source mismatch: '+name)
    work.mkdir(parents=True)
    bundles={}
    if args.bundle:
        bundles={p['repository']:p for p in json.loads((args.bundle/'bundle.json').read_text(encoding='utf8'))['packages']}
    hashes={}
    for folder,package in [('maibot-maivita-plugin','maivita'),('maibot-maiIdentity-plugin','maiidentity'),
                           ('maibot-maihush-plugin','maihush'),('maibot-maicoordinate-plugin','maicoordinate')]:
        source=ROOT/folder; target=work/'plugins'/folder; target.mkdir(parents=True)
        packaged=folder in bundles
        if packaged:
            entry=bundles[folder]; archive=args.bundle/entry['archive']
            if digest(archive)!=entry['sha256']: raise ValueError('archive hash mismatch')
            with zipfile.ZipFile(archive) as package_zip:
                for name in package_zip.namelist():
                    dest=(work/'plugins'/name).resolve()
                    if not dest.is_relative_to(target.resolve()): raise ValueError('unsafe archive member')
                package_zip.extractall(work/'plugins')
        for name in ('plugin.py','_manifest.json'):
            if not packaged: shutil.copyfile(source/name,target/name)
            if digest(target/name)!=digest(source/name): raise ValueError('packaged source mismatch')
            hashes[folder+'/'+name]=digest(source/name)
        for path in (source/package).rglob('*'):
            if path.is_file() and path.suffix in ('.py','.sql','.json') and '__pycache__' not in path.parts:
                relative=path.relative_to(source); dest=target/relative; dest.parent.mkdir(parents=True,exist_ok=True)
                if not packaged: shutil.copyfile(path,dest)
                if digest(dest)!=digest(path): raise ValueError('packaged source mismatch')
                hashes[folder+'/'+relative.as_posix()]=digest(path)
        if not packaged:
            (target/'_locales').mkdir(exist_ok=True); (target/'_locales/zh-CN.json').write_text('{}')
    shutil.copyfile(host/'pyproject.toml',work/'pyproject.toml')
    if args.space:
        if not args.maitrace_source: raise ValueError('--maitrace-source required for space acceptance')
        trace=work/'plugins/MaiTrace'
        if 'MaiTrace' in bundles:
            entry=bundles['MaiTrace'];archive=args.bundle/entry['archive']
            if digest(archive)!=entry['sha256']:raise ValueError('MaiTrace archive hash mismatch')
            with zipfile.ZipFile(archive) as package_zip:
                for name in package_zip.namelist():
                    dest=(work/'plugins'/name).resolve()
                    if not dest.is_relative_to(trace.resolve()):raise ValueError('unsafe MaiTrace archive member')
                    if hashlib.sha256(package_zip.read(name)).hexdigest()!=entry['files'][name]:raise ValueError('MaiTrace entry hash mismatch')
                package_zip.extractall(work/'plugins')
        else:
            shutil.copytree(args.maitrace_source,trace,ignore=shutil.ignore_patterns('.git','__pycache__','config.toml','data'))
        fixture=ROOT/'maibot-maivita-plugin/ecosystem/maitrace/tests/native_fixture.py'
        shutil.copyfile(fixture,trace/'native_fixture.py')
        # Explicit test-only IO replacement in the staged copy, never release source.
        with (trace/'plugin.py').open('a',encoding='utf-8') as f:
            f.write('\nfrom .native_fixture import install as _install_native_fixture\n_install_native_fixture()\n')
    if args.server_probe_source:
        probe=work/'plugins/phase1-server-probe'
        shutil.copytree(args.server_probe_source,probe)
        import tomllib,tomli_w
        cfg=tomllib.loads((probe/'config.toml').read_text('utf-8'))
        assert cfg['execute'] is False
        cfg['plugin']['enabled']=False
        (probe/'config.toml').write_text(tomli_w.dumps(cfg),encoding='utf-8')
    safety=work/'safety'; safety.mkdir()
    socket_dir=tempfile.TemporaryDirectory(prefix='joint-rpc-')
    socket_path=str(Path(socket_dir.name)/'rpc.sock')
    (safety/'sitecustomize.py').write_text("import sys,os\ndef audit(event,args):\n    if event=='socket.connect':\n        a=args[1]\n        if a==os.environ.get('MAIHUSH_TEST_SOCKET_PATH'): return\n        if not isinstance(a,tuple) or a[0] not in ('127.0.0.1','::1'): raise PermissionError('JOINT_TEST_NETWORK_DENIED')\nsys.addaudithook(audit)\n")
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','NUMBER_OF_PROCESSORS')}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',
        PYTHONPATH=os.pathsep.join((str(safety),str(ROOT/'tools'),str(host))),
        MAIBOT_HOST_VERSION=host_version,JOINT_AUTO_ROUTES=str(int(args.auto_routes)),
        JOINT_TRUSTED_MANAGEMENT=str(int(args.trusted_management)),
        JOINT_SPACE=str(int(args.space)),
        JOINT_SERVER_PROBE=str(int(bool(args.server_probe_source))),
        MAIBOT_INSTANCE_DIR=str(work),TEMP=str(work),TMP=str(work),USERPROFILE=str(work))
    if os.name!='nt': env['MAIHUSH_TEST_SOCKET_PATH']=socket_path
    process=subprocess.Popen([sys.executable,'-m','joint_native_scenario'],cwd=work,env=env,stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
    try: output,_=process.communicate(timeout=150)
    except BaseException:
        if process.poll() is None:
            if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else: os.killpg(process.pid,signal.SIGKILL)
            process.wait()
        raise
    finally: socket_dir.cleanup()
    (work/'console.log').write_bytes(output)
    assert hashes=={name:digest(ROOT/name) for name in hashes}
    report={'host_commit':lock['host_commit'],'host':host_version,'sdk':sdk,'host_files_verified':len(lock['host_files']),
        'sdk_files_verified':len(lock['sdk_files']),'runtime_hashes':hashes,'exit_code':process.returncode,
        'real_model_requests':0,'real_platform_sends':0,'source_unchanged':True}
    report['installed_bundle']=str(args.bundle) if args.bundle else None
    report['host_extension']=lock.get('extension')
    console=output.decode('utf8',errors='replace')
    report['hook_action_contract_passed']='未知的 Hook action' not in console
    report['graceful_shutdown_passed']=not any(s in console for s in ('Runner shutdown 请求失败','Runner 优雅退出超时','尝试 kill'))
    assert verification=={name:digest(ROOT/'tools'/name) for name in verification}
    report['verification_files']=verification
    (work/'run.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    assert report['hook_action_contract_passed'],'unsupported Hook action returned'
    print((work/'result.json').read_text() if process.returncode==0 else output.decode('utf8',errors='replace')[-6500:])
    raise SystemExit(process.returncode)


if __name__=='__main__': main()
