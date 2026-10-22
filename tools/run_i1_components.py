"""Contain the full Host component lifecycle test in a Windows process job."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from integration_tests.process_job import ProcessJob
from tools.package import runtime_manifest as file_manifest

if __name__=='__main__':
    (root/'evidence/integration-ready').mkdir(parents=True,exist_ok=True)
    host=Path(os.environ.get('MAIVITA_HOST_SOURCE',root.parent/'maibot')); instance=Path(os.environ.get('MAIVITA_TEST_INSTANCE',root/'.integration/ready-instance'))
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT')}
    env.update(PYTHONPATH=os.pathsep.join((str(root),str(host))),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
        MAIBOT_INSTANCE_DIR=str(instance),MAIVITA_I1_SOURCE=str(root),MAIBOT_WORKER_PROCESS='1')
    env['MAIVITA_EXPECT_ENGINE']=os.environ.get('MAIVITA_EXPECT_ENGINE','life')
    # Session explicitly authorizes these agreements for this isolated instance.
    for filename,key in [('EULA.md','EULA_AGREE'),('PRIVACY.md','PRIVACY_AGREE')]:
        env[key]=hashlib.md5((host/filename).read_text(encoding='utf-8').encode()).hexdigest()
    log=instance/'logs/i1-components-console.log'
    with ProcessJob() as job,log.open('wb') as stream:
        interpreter=Path(os.environ.get('MAIVITA_HOST_PYTHON',root/'.integration/i1-host-env/Scripts/python.exe'))
        p=subprocess.Popen([str(interpreter),'-m','integration_tests.i1_components'],
            cwd=instance,env=env,stdin=subprocess.PIPE,stdout=stream,stderr=subprocess.STDOUT)
        try:
            job.assign(p); p.communicate(input=b'1',timeout=100)
        finally:
            job.close()
            if p.poll() is None: p.kill(); p.wait()
    path=instance/'i1-components.json'
    report=json.loads(path.read_text()) if path.exists() else {}
    report.update(process_exit_code=p.returncode,passed=p.returncode==0,candidate_files=file_manifest())
    filename='legacy-components.json' if env['MAIVITA_EXPECT_ENGINE']=='legacy' else 'components.json'
    (root/'evidence/integration-ready'/filename).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='candidate_files'},indent=2))
    raise SystemExit(p.returncode!=0)
