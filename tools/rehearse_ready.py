"""Real Host offline engine roundtrip and checksum-verified restore; synthetic only."""
from contextlib import closing
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import tomli_w
from uuid import uuid4
from switch_engine import prepare

root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from tools.package import runtime_manifest
from maivita.life.storage.safety import WriterLease


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--instance',required=True,type=Path)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(); instance=args.instance
    if not instance.resolve().is_relative_to(root/'.integration') or json.loads((instance/'.maivita-i1-test-instance.json').read_text())['production_data'] is not False:
        raise ValueError('workspace synthetic instance required')
    data=instance/'data/plugins/davidblackcn.maibot-maivita-plugin'
    config=instance/'plugins/maibot-maivita-plugin/config.toml'
    current=tomllib.loads(config.read_text(encoding='utf-8'))
    current['legacy']['admin']['legacy_admin_users']=['synthetic-admin']
    current['legacy']['planning']['daily_enabled']=False
    current['legacy']['compatibility']['enable_proactive_trigger']=False
    current['model']['generate_on_start']=False
    run_id=uuid4().hex
    source=instance/('transition-source-'+run_id+'.toml')
    with source.open('wb') as file: tomli_w.dump(current,file)
    legacy=instance/('transition-legacy-'+run_id+'.toml')
    active=json.loads((data/'engine-authority.json').read_text())
    if active['engine']=='legacy':
        if not args.resume: raise ValueError('explicit --resume needed for already activated legacy profile')
    else:
        ticket=prepare(source,data,'legacy',legacy)
        config.write_bytes(legacy.read_bytes())
    env={**os.environ,'MAIVITA_TEST_INSTANCE':str(instance),'MAIVITA_EXPECT_ENGINE':'legacy'}
    # This wrapper contains the complete original MainSystem and plugin Runner.
    subprocess.run([sys.executable,str(root/'tools/run_i1_components.py')],cwd=root,env=env,check=True)
    back=instance/('transition-life-'+run_id+'.toml')
    reverse=prepare(config,data,'life',back)
    config.write_bytes(back.read_bytes())
    env['MAIVITA_EXPECT_ENGINE']='life'
    subprocess.run([sys.executable,str(root/'tools/run_i1_components.py')],cwd=root,env=env,check=True)
    # Both native Host processes are stopped before reading/restoring snapshots.
    with WriterLease(instance/'maivita-writer.lock'):
        restored=[]
        with tempfile.TemporaryDirectory(prefix='maivita-ready-restore-',dir=root/'.integration') as temporary:
            target_root=Path(temporary).resolve()
            for item in reverse['snapshots']:
                source=data/item['path']; target=target_root/(item['engine']+'.db')
                if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:
                    raise ValueError('rollback snapshot changed')
                shutil.copyfile(source,target)
                if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:
                    raise ValueError('restore checksum mismatch')
                with closing(sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
                    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if item['engine']=='life':
                        state=db.execute('SELECT state_version FROM life_state').fetchone()[0]
                        events=db.execute('SELECT count(*) FROM life_events').fetchone()[0]
                    else: state=events=None; assert 'goals' in tables
                restored.append({'engine':item['engine'],'sha256':item['sha256'],'state_version':state,'events':events})
        authority=json.loads((data/'engine-authority.json').read_text())
        assert authority['engine']=='life' and authority['revision']==reverse['from_revision']+1
    result={'passed':True,'native_legacy_and_life':True,'single_writer':True,'rollback_snapshots':restored,
            'authority_revision':authority['revision'],'restored_temporary_files_removed':True,
            'runtime_files':runtime_manifest(),'production_data':False,'automatic_data_downgrade':False}
    path=root/'evidence/integration-ready/restore.json'; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='runtime_files'},indent=2))


if __name__=='__main__': main()
