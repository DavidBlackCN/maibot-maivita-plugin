"""Complete-Host startup/registration smoke, with real WebUI authentication."""
from pathlib import Path
import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import tomlkit

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from integration_tests.process_job import ProcessJob
from tools.package import runtime_manifest as file_manifest,HOST_COMMIT

HOST=Path(os.environ.get('MAIVITA_HOST_SOURCE',ROOT.parent/'maibot'))
INSTANCE=Path(os.environ.get('MAIVITA_TEST_INSTANCE',ROOT/'.integration/ready-instance'))
PYTHON=Path(os.environ.get('MAIVITA_HOST_PYTHON',ROOT/'.integration/i1-host-env/Scripts/python.exe'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agreements-confirmed',action='store_true',help='operator has explicitly agreed to Host EULA and privacy for this test')
    parser.add_argument('--exercise-model',action='store_true',help='one configured startup decision using the dedicated real provider')
    args=parser.parse_args()
    if not args.agreements_confirmed: parser.error('explicit user agreement required before starting Host')
    (ROOT/'evidence/integration-ready').mkdir(parents=True,exist_ok=True)
    marker=json.loads((INSTANCE/'.maivita-i1-test-instance.json').read_text(encoding='utf-8'))
    if marker.get('production_data') is not False: raise ValueError('test-instance marker missing')
    config=tomllib.loads((INSTANCE/'config/bot_config.toml').read_text(encoding='utf-8'))
    if config['telemetry']['enable'] is not False or config['maim_message']['enabled'] is not False or config['webui']['host']!=['127.0.0.1']:
        raise ValueError('I1 requires telemetry/platform server disabled and loopback-only WebUI')
    plugin_config=INSTANCE/'plugins/maibot-maivita-plugin/config.toml'
    original_plugin_config=plugin_config.read_bytes()
    if args.exercise_model:
        model_config=tomllib.loads((INSTANCE/'config/model_config.toml').read_text(encoding='utf-8'))
        providers=model_config.get('api_providers',[])
        if not any(p.get('api_key') and p['api_key']!='your-api-key' for p in providers):
            raise ValueError('dedicated real-model configuration not present')
        plugin=tomlkit.parse(original_plugin_config.decode('utf-8').replace('\r\n','\n'))
        plugin['model']['generate_on_start']=True
        plugin_config.write_bytes(tomlkit.dumps(plugin).encode('utf-8'))
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT')}
    env.update(PYTHONPATH=str(HOST),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
        MAIBOT_INSTANCE_DIR=str(INSTANCE),MAIVITA_I1_START_GATE='1',PYTHONUNBUFFERED='1')
    # User explicitly agreed in this session. Use the Host's own documented
    # checksum environment mechanism; never write counterfeit confirmation files.
    for filename,key in [('EULA.md','EULA_AGREE'),('PRIVACY.md','PRIVACY_AGREE')]:
        env[key]=hashlib.md5((HOST/filename).read_text(encoding='utf-8').encode()).hexdigest()
    report={'host_commit':HOST_COMMIT,'sdk':'2.15.0','candidate_files':file_manifest(),
        'complete_host_started':False,'real_model_requests':0,'passed_cases':[],
        'agreements':'explicit user approval; Host checksum mechanism','telemetry':False,
        'production_data':False,'passed':False,'first_install_gate':False,'scope':'complete original Host CLI, no module replacement'}
    base='http://127.0.0.1:'+str(config['webui']['port'])
    log=INSTANCE/'logs/i1-console.log'
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),urllib.request.ProxyHandler({}))
    def request(path,payload=None):
        data=json.dumps(payload).encode() if payload is not None else None
        with opener.open(urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'}),timeout=5) as response:
            return json.load(response)
    process=None
    try:
        with ProcessJob() as job,log.open('wb') as stream:
            process=subprocess.Popen([str(PYTHON),str(ROOT/'tools/i1_full_host_entry.py')],cwd=INSTANCE,env=env,
                stdin=subprocess.PIPE,stdout=stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            job.assign(process); process.stdin.write(b'1'); process.stdin.flush()
            deadline=time.monotonic()+80
            while time.monotonic()<deadline and process.poll() is None:
                try:
                    request('/api/webui/health')
                    report['complete_host_started']=True; break
                except (OSError,ValueError): time.sleep(.5)
            if not report['complete_host_started']:
                report['error']='complete Host did not expose WebUI within startup window'
            else:
                token=json.loads((INSTANCE/'data/webui.json').read_text(encoding='utf-8'))['access_token']
                # Exercise real auth first. The token never enters evidence.
                response=request('/api/webui/auth/verify',{'token':token})
                report['authentication_verified']=bool(response.get('valid',response.get('success',False)))
                report['auth_response_fields']=sorted(response)
                report['passed_cases'].append('startup')
                plugin='davidblackcn.maibot-maivita-plugin'
                report['components']=request('/api/webui/plugins/runtime/plugins/'+plugin+'/components')
                def query(name):
                    response=request('/api/webui/plugins/runtime/webui/'+plugin+'/status/queries/'+name,{'args':{}})
                    if not response.get('success'): raise ValueError('native WebUI API invocation failed')
                    return response['result']
                until=time.monotonic()+12
                while True:
                    try:
                        state=query('state')
                        if state.get('ok'): break
                    except (OSError,ValueError): pass
                    if time.monotonic()>until: raise RuntimeError('candidate API did not become ready')
                    time.sleep(.3)
                if args.exercise_model:
                    until=time.monotonic()+30
                    while time.monotonic()<until:
                        diagnostic=query('diagnostics')
                        model=diagnostic.get('data',{}).get('model',{})
                        if model.get('status') not in ('not_called',None): break
                        time.sleep(.5)
                    report['model_port']=model
                    report['startup_decision']=diagnostic.get('data',{}).get('startup_decision')
                    if model.get('status')=='candidate_validated' and report['startup_decision'].get('status')=='applied':
                        report['real_model_requests']=1
                        report['passed_cases'].append('model')
                state=query('state'); plan=query('plan'); legacy=query('legacy'); diagnostic=query('diagnostics')
                report['api_state']=state; report['api_plan']=plan; report['api_legacy']=legacy
                report['api_diagnostics']=diagnostic
                assert state['ok'] and plan['ok'] and diagnostic['ok']
                assert 'api_version' not in legacy
                report['passed_cases'].append('api')
                denied=request('/api/webui/plugins/runtime/webui/'+plugin+'/status/actions/denied_replan',{'args':{}})
                assert denied['result']['error']['code']=='PERMISSION_DENIED'
                report['passed_cases'].append('permission_denial')
                # Real authenticated Host enable/disable path stops and starts
                # the plugin. Disable startup generation before re-enable so
                # the lifecycle check does not incur another provider call.
                toggled=request('/api/webui/plugins/config/'+plugin+'/toggle',{})
                report['disable_result']=toggled
                report['unloaded_database_preserved']=(INSTANCE/'data/plugins'/plugin/'life/maivita.db').is_file()
                on_disk=tomlkit.parse(plugin_config.read_text(encoding='utf-8'))
                on_disk['model']['generate_on_start']=False
                plugin_config.write_bytes(tomlkit.dumps(on_disk).encode('utf-8'))
                enabled=request('/api/webui/plugins/config/'+plugin+'/toggle',{})
                report['enable_result']=enabled
                until=time.monotonic()+10
                while True:
                    try:
                        after=query('state')
                        if after.get('ok'): break
                    except (OSError,ValueError): pass
                    if time.monotonic()>until: raise RuntimeError('reload API not ready')
                    time.sleep(.3)
                report['post_reload_state']=after
                report['passed_cases'].append('reload')
                report['plugin_database_created']=(INSTANCE/'data/plugins'/plugin/'life/maivita.db').is_file()
                # Runtime endpoints are queried only after successful cookie auth.
                report['webui_ready']=True
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                try: process.wait(timeout=55)
                except subprocess.TimeoutExpired: report['shutdown_forced']=True
            report['host_exit_code']=process.poll()
            job.close()
            if process.poll() is None: process.wait(timeout=5)
    except Exception as error:
        report['error']=type(error).__name__+': '+str(error)
    finally:
        if process is not None:
            if process.poll() is None: process.kill(); process.wait()
            if process.stdin: process.stdin.close()
        # Only our temporary startup flag/toggle changes are rolled back; the
        # Host model config (which contains the user's key) is never rewritten.
        plugin_config.write_bytes(original_plugin_config)
        lines=log.read_text(encoding='utf-8',errors='replace').splitlines() if log.exists() else []
        safe=[line for line in lines if not re.search(r'token|cookie|authorization|api.?key|密钥|令牌',line,re.I)]
        report['sanitized_log_tail']=safe[-20:]
        report['real_model_acceptance']='passed_candidate_commit' if 'model' in report['passed_cases'] else 'not_passed'
        report['smoke_passed']=(not report.get('error') and {'startup','api','permission_denial','reload'}<=set(report['passed_cases']) and
            (not args.exercise_model or 'model' in report['passed_cases']) and report.get('host_exit_code')==0)
        (ROOT/'evidence/integration-ready/full-host-smoke.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({key:value for key,value in report.items() if key not in ('candidate_files','sanitized_log_tail')},indent=2))
    return not report['smoke_passed']


if __name__=='__main__': raise SystemExit(main())
