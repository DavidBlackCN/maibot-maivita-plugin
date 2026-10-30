"""Assemble a pinned upstream archive plus reviewed patches. No network or credentials."""
from pathlib import Path
import argparse,hashlib,io,json,subprocess,zipfile

ROOT=Path(__file__).resolve().parent
PIN='712c777aab869c68808f186c24dddf4cab3e36d1'


def build(source,output):
    output=output.resolve()
    if output.exists(): raise ValueError('Output must be a new directory')
    if source.is_file():
        pin=json.loads((ROOT/'pin.json').read_text('utf-8'))
        archive=source.read_bytes()
        if pin['commit']!=PIN or hashlib.sha256(archive).hexdigest()!=pin['archive_sha256']:raise ValueError('Upstream archive mismatch')
    else:
        actual=subprocess.check_output(['git','-C',str(source),'rev-parse',PIN+'^{commit}'],text=True).strip()
        if actual!=PIN: raise ValueError('Upstream commit mismatch')
        archive=subprocess.check_output(['git','-C',str(source),'archive','--format=zip',PIN])
    output.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        if any(not (output/p).resolve().is_relative_to(output) for p in z.namelist()): raise ValueError('Unsafe upstream archive')
        z.extractall(output)
    patches=[]
    for patch in sorted((ROOT/'patches').glob('*.patch')):
        subprocess.run(['git','apply','--check',str(patch)],cwd=output,check=True)
        subprocess.run(['git','apply',str(patch)],cwd=output,check=True)
        patches.append({'file':patch.name,'sha256':hashlib.sha256(patch.read_bytes()).hexdigest()})
    if not patches: raise ValueError('Missing adaptation patches')
    record={'upstream':'https://github.com/Rabbit-Jia-Er/MaiTrace','commit':PIN,
        'upstream_version':'6.3.0','adaptation':'vita-coordinate-1','patches':patches,
        'files':{p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob('*')) if p.is_file()}}
    (output/'ADAPTATION.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=build(a.source,a.output);print(json.dumps({k:v for k,v in r.items() if k!='files'},indent=2))
