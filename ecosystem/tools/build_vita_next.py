"""Build the life release only from source-matched, successful isolated runs."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import xml.etree.ElementTree as ET
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--native',type=Path,action='append',required=True)
    p.add_argument('--junit',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.workspace.resolve()
    baseline=json.loads((root/'MaiTogether/baseline.json').read_text(encoding='utf8'))
    sources={name:root/name for name in ('maibot-maivita-plugin','maibot-maicoordinate-plugin',
        'maibot-maiIdentity-plugin','maibot-maihush-plugin')}
    sources.update(adapter=root/'.implementation/behavior-control/adapter',poke=root/'.implementation/vendor/smart_poke_plugin',
        cateye=root/'.implementation/vendor/Cateye-audit',date=root/'.implementation/vendor/date_context_plugin')
    evidence={}
    for directory in args.native:
        accepted=json.loads((directory/'acceptance.json').read_text(encoding='utf8'))
        data=(directory/'sources.json').read_bytes()
        if accepted.get('passed') is not True or accepted['baseline']!=baseline or accepted['sources_sha256']!=sha(data):
            raise ValueError('Unsuccessful or inconsistent native evidence: '+str(directory))
        for path,digest in json.loads(data).items():
            name,relative=path.split('/',1)
            if sha((sources[name]/relative).read_bytes())!=digest:raise ValueError('Source changed after test: '+path)
        evidence[accepted['kind']]={'cases':accepted['cases'],'sources_sha256':sha(data)}
    if set(evidence)!={'vita-next','community','sleep','behavior'}:
        raise ValueError('All four native scenarios are required')
    suites=[]
    for path in args.junit:
        tree=ET.parse(path);cases=list(tree.iter('testcase'))
        if not cases or list(tree.iter('failure')) or list(tree.iter('error')):raise ValueError('Failed or empty suite: '+str(path))
        suites.append({'file':path.name,'sha256':sha(path.read_bytes()),'tests':len(cases),'skipped':len(list(tree.iter('skipped')))})
    if len(suites)!=3:raise ValueError('Vita, Coordinate and MaiTogether test results are required')
    # Reuse Vita's distribution allowlist, without substituting historical release gates.
    namespace=types.ModuleType('vita_release_tools');namespace.__path__=[str(root/'maibot-maivita-plugin/tools')]
    sys.modules[namespace.__name__]=namespace
    vita=importlib.import_module('vita_release_tools.package').entries()
    coordinate={name:(root/'maibot-maicoordinate-plugin'/name).read_bytes() for name in
        ('plugin.py','_manifest.json','LICENSE','README.md','UPDATE_NOTES.md','config.example.toml')}
    for directory in ('maicoordinate','_locales'):
        for path in (root/'maibot-maicoordinate-plugin'/directory).rglob('*'):
            if path.is_file() and path.suffix in ('.py','.json'):
                coordinate[path.relative_to(root/'maibot-maicoordinate-plugin').as_posix()]=path.read_bytes()
    args.output.mkdir(parents=True,exist_ok=False)
    archives={}
    for folder,files in (('maibot-maivita-plugin',vita),('maibot-maicoordinate-plugin',coordinate)):
        version=json.loads(files['_manifest.json'])['version']
        name=folder+'-'+version+'.zip'
        hashes={key:sha(value) for key,value in sorted(files.items())}
        with zipfile.ZipFile(args.output/name,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for relative,data in sorted({**files,'FILES.sha256.json':json.dumps(hashes,indent=2).encode()}.items()):
                info=zipfile.ZipInfo(folder+'/'+relative,date_time=(2026,11,4,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data)
        archives[name]=sha((args.output/name).read_bytes())
    (args.output/'release.json').write_text(json.dumps({'baseline':baseline,'native':evidence,
        'unit':suites,'archives':archives,'real_accounts_or_paid_models_tested':False},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(archives,indent=2))


if __name__=='__main__':main()
