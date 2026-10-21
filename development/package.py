"""Allowlisted, deterministic staging; distributable ZIP requires I1 acceptance."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
HOST_COMMIT='5c7637e6913c84d1c3d5e742fa9e95a7e70d3501'
PACKAGES=('domain','life_state','planning','coordinator','continuity','events','storage',
          'cache','core','database','handlers','planner','services','utils','integrations','compatibility')
READMES=('INSTALL.md','LIMITATIONS.md','ACCEPTANCE.md','ROLLBACK.md')


def entries():
    files={}
    for directory in PACKAGES:
        for path in (ROOT/directory).rglob('*'):
            if path.is_file() and path.suffix in ('.py','.sql'):
                files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    for directory,names in {
        'development':('__init__.py','config.py','model_port.py','plugin.py')}.items():
        for name in names: files[directory+'/'+name]=(ROOT/directory/name).read_bytes()
    for name in READMES: files[name]=(ROOT/'docs'/name).read_bytes()
    for name in ('INSTALL.md','LIMITATIONS.md','ACCEPTANCE.md','ROLLBACK.md','CONTRACTS.md','SOURCES.md','CHANGELOG.md'):
        files['docs/'+name]=(ROOT/'docs'/name).read_bytes()
    schema=ROOT/'docs/contracts-v1.schema.json'
    if schema.exists(): files['docs/contracts-v1.schema.json']=schema.read_bytes()
    public_proof=ROOT/'docs/integration-ready.json'
    if public_proof.exists(): files['docs/integration-ready.json']=public_proof.read_bytes()
    for name in ('I1_INTEGRATION_READY_REPORT.md','DEVELOPMENT_PLAN_R0-R4.md','MIGRATION_PLAN.md','ARCHITECTURE.md','MAIVITA_DESIGN_SPEC.md'):
        path=ROOT/'docs/development'/name
        if path.exists(): files['docs/development/'+name]=path.read_bytes()
    files['LICENSE']=(ROOT/'LICENSE').read_bytes()
    files['SOURCES.md']=(ROOT/'docs/SOURCES.md').read_bytes()
    for name in ('plugin.py','entrypoint.py','integration_config.py','activation.py','legacy_plugin.py','config_models.py','README.md'):
        files[name]=(ROOT/name).read_bytes()
    files['utils/__init__.py']=b''
    files['__init__.py']=(ROOT/'__init__.py').read_bytes()
    files['_manifest.json']=(ROOT/'_manifest.json').read_bytes()
    files['webui.json']=(ROOT/'webui.json').read_bytes()
    files['config.example.toml']=(ROOT/'config.example.toml').read_bytes()
    for name in ('convert_config.py','r0_migrate.py','stage_legacy.py','switch_engine.py','requirements.txt'):
        files['tools/'+name]=(ROOT/'tools'/name).read_bytes()
    return files


def file_manifest():
    return {name:hashlib.sha256(data).hexdigest() for name,data in sorted(entries().items())}


def runtime_manifest():
    return {name:sha for name,sha in file_manifest().items() if not name.endswith('.md') and not name.startswith('docs/')}


def stage(destination):
    destination=Path(destination)
    if not destination.is_absolute() or destination.exists():
        raise ValueError('staging requires a new absolute directory')
    for relative,data in entries().items():
        target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(data)
    manifest=file_manifest()
    (destination/'FILES.sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def build_zip(destination,acceptance):
    # Absence, failure, stale source or synthetic-only evidence never produces
    # an installable distribution. Staging is only for isolated testing.
    required={'startup','model','api','tool','command','hook','reload','shutdown','single_writer','restore','permission_denial'}
    if (acceptance.get('host_commit')!=HOST_COMMIT or acceptance.get('sdk')!='2.15.0' or
        acceptance.get('complete_host_started') is not True or acceptance.get('real_model_requests',0)<1 or
        acceptance.get('passed') is not True or set(acceptance.get('passed_cases',[]))!=required or
        acceptance.get('integration_ready') is not True or
        acceptance.get('runtime_files')!=runtime_manifest()):
        raise ValueError('I1 first-install acceptance incomplete or source changed; no ZIP generated')
    destination=Path(destination)
    if not destination.is_absolute(): raise ValueError('absolute artifact path required')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(entries().items()):
            info=zipfile.ZipInfo('maibot-maivita-plugin/'+name,date_time=(2026,10,21,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
        info=zipfile.ZipInfo('maibot-maivita-plugin/FILES.sha256.json',date_time=(2026,10,21,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,json.dumps(file_manifest(),indent=2))
    return hashlib.sha256(destination.read_bytes()).hexdigest()
