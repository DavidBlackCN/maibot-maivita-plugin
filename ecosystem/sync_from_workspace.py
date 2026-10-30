"""Refresh the existing coordinator and native harness in MaiVita version control.

Run with --workspace pointing to the parent of the four existing source folders.
Never copies runtime data, credentials, local config, environments, or evidence.
"""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);a=p.parse_args()
    root=a.workspace.resolve();target=root/'maibot-maivita-plugin/ecosystem';target.mkdir(exist_ok=True)
    source=root/'maibot-maicoordinate-plugin';names={'.gitignore','plugin.py','_manifest.json','README.md','UPDATE_NOTES.md','LICENSE','config.example.toml'}
    for directory in ('maicoordinate','tests','_locales'):
        names.update(f.relative_to(source).as_posix() for f in (source/directory).rglob('*') if f.is_file() and f.suffix in ('.py','.json') and '__pycache__' not in f.parts)
    hashes={}
    for name in sorted(names):
        f=source/name
        if not f.exists():continue
        dst=target/'coordinator'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(f,dst)
        hashes[name]=hashlib.sha256(f.read_bytes()).hexdigest()
    (target/'coordinator-source.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf8',newline='\n')
    (target/'tools').mkdir(exist_ok=True)
    for name in ('run_joint_native.py','joint_native_scenario.py','phase1_native_space.py','vita_context_native.py','product_bundle.py'):shutil.copyfile(root/'tools'/name,target/'tools'/name)
    pins={}
    for key,folder in (('identity','maibot-maiIdentity-plugin'),('hush','maibot-maihush-plugin')):
        repo=root/folder
        pins[key]=subprocess.check_output(['git','-c','safe.directory='+repo.as_posix(),'-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    (target/'peers.json').write_text(json.dumps(pins,indent=2)+'\n',encoding='utf8',newline='\n')
    print('Synchronized coordinator, harness and immutable peer revisions:',pins)
if __name__=='__main__':main()
