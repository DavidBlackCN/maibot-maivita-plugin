"""Stage the actual repository entry into a new, explicitly isolated full Host."""
from pathlib import Path
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tomlkit

root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from development.package import stage,HOST_COMMIT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True,type=Path)
    parser.add_argument('--python',required=True,type=Path)
    parser.add_argument('--instance',required=True,type=Path)
    parser.add_argument('--test-model-config',type=Path)
    args=parser.parse_args()
    if not all(p.is_absolute() for p in (args.host,args.python,args.instance)):
        raise ValueError('absolute Host, interpreter and instance paths required')
    if args.instance.exists(): raise ValueError('new empty instance required; never overwrite an instance')
    if not args.instance.resolve().is_relative_to(root/'.integration'):
        raise ValueError('this helper writes only inside workspace .integration')
    lock=json.loads((args.host/'.maivita-i1-source.json').read_text())
    if lock['host_commit']!=HOST_COMMIT: raise ValueError('audited Host source required')
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT')}
    env.update(PYTHONPATH=str(args.host),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([str(args.python),'-m','src.cli.tool','init','--directory',str(args.instance)],cwd=args.host,env=env,check=True)
    bot_path=args.instance/'config/bot_config.toml'
    bot=tomlkit.parse(bot_path.read_text(encoding='utf-8'))
    bot['telemetry']['enable']=False; bot['maim_message']['enabled']=False; bot['mcp']['enable']=False
    bot['plugin_runtime']['render']['enabled']=False; bot['webui']['host']=['127.0.0.1']
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); bot['webui']['port']=listener.getsockname()[1]
    bot_path.write_bytes(tomlkit.dumps(bot).encode('utf-8'))
    (args.instance/'.maivita-i1-test-instance.json').write_text(json.dumps({'host_commit':HOST_COMMIT,
        'production_data':False,'telemetry_enabled':False,'purpose':'Integration Ready synthetic instance'}),encoding='utf-8')
    target=args.instance/'plugins/maibot-maivita-plugin'; stage(target)
    cfg=tomlkit.parse((target/'config.example.toml').read_text(encoding='utf-8'))
    cfg['plugin']['enabled']=True
    cfg['instance']['acknowledgement']='isolated-test-instance'
    cfg['instance']['writer_lock']=str(args.instance/'maivita-writer.lock')
    cfg['instance']['allowed_streams']=['maivita-i1-synthetic-test-stream']
    # The automated full-Host acceptance instance permits one real decision
    # attempt per 24h, including uncertain outcomes. Reloads cannot spend more.
    cfg['model']['daily_budget']=1
    (target/'config.toml').write_bytes(tomlkit.dumps(cfg).encode('utf-8'))
    if args.test_model_config:
        # Explicit operator-selected TEST configuration only; never print it.
        if not args.test_model_config.is_absolute(): raise ValueError('absolute dedicated test model config required')
        shutil.copyfile(args.test_model_config,args.instance/'config/model_config.toml')
    print(json.dumps({'prepared':True,'instance':str(args.instance),'production_data':False,'host_started':False}))


if __name__=='__main__': main()
