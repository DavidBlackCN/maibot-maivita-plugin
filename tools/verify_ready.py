"""Credential-free static/unit/native Runner gates. Full Host/model is separate."""
from pathlib import Path
import ast
import hashlib
import json
import os
import subprocess
import sys
import types

root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from tools.package import runtime_manifest


def verification_manifest():
    names=subprocess.check_output(['git','-c','safe.directory='+root.as_posix(),
        'ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().strip('\0').split('\0')
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(set(names))
            if (root/name).is_file() and Path(name).suffix in ('.py','.sql','.json','.toml','.txt','.yml','.yaml')
            and not name.startswith('docs/')}


def main():
    evidence=root/'evidence/integration-ready'; evidence.mkdir(parents=True,exist_ok=True)
    before=verification_manifest()
    for name in before:
        if name.endswith('.py'):
            text=(root/name).read_text(encoding='utf-8-sig')
            ast.parse(text,filename=name); compile(text,name,'exec',dont_inherit=True)
        elif name.endswith('.json'): json.loads((root/name).read_text(encoding='utf-8'))
    package=types.ModuleType('maivita_static'); package.__path__=[str(root)]; sys.modules[package.__name__]=package
    from maivita_static.maivita.host.integration_config import IntegrationConfig
    from maivita_static.maivita.integrations import contracts
    import tomllib
    config=IntegrationConfig.model_validate(tomllib.loads((root/'config.example.toml').read_text(encoding='utf-8')))
    assert not config.plugin.enabled and config.engine.mode=='life'
    generated={name:getattr(contracts,name).model_json_schema() for name in
               ('IdentityRequest','IdentityDecision','PolicyRequest','PolicyDecision','SocialEvent','SocialAck')}
    assert json.loads((root/'docs/contracts-v1.schema.json').read_text())==generated
    result={'static':True,'runtime_files':runtime_manifest(),'verification_files':before}
    commands={
        'unittest':[sys.executable,str(root/'tools/run_tests.py'),'--report-prefix','integration-ready-regression'],
        'pytest':[sys.executable,'-m','pytest','tests','-q','-p','no:cacheprovider'],
        'source':[sys.executable,str(root/'tools/setup_i0_host.py'),'--offline'],
        'runner':[sys.executable,'-m','unittest','integration_tests.test_environment','-v']}
    for name,command in commands.items():
        process=subprocess.run(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (evidence/(name+'.txt')).write_bytes(process.stdout)
        result[name]={'passed':process.returncode==0,'exit_code':process.returncode}
        if name=='pytest': result[name]['summary']=process.stdout.decode('utf-8',errors='replace').splitlines()[-1]
        print(json.dumps({name:result[name]}),flush=True)
    result['source_unchanged_during_checks']=before==verification_manifest()
    result['passed']=result['source_unchanged_during_checks'] and all(result[name]['passed'] for name in commands)
    (evidence/'offline-checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return not result['passed']


if __name__=='__main__': raise SystemExit(main())
