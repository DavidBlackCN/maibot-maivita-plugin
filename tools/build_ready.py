"""Evaluate fresh evidence; only a passed Integration-Ready gate can emit ZIP."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
import subprocess

root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from tools.package import runtime_manifest,file_manifest,entries,build_zip,HOST_COMMIT,SDK_VERSION
from verify_ready import verification_manifest


def evaluate():
    directory=root/'evidence/integration-ready'
    read=lambda name:json.loads((directory/name).read_text(encoding='utf-8'))
    offline,smoke,components,legacy,restore,audit=[read(name) for name in
        ('offline-checks.json','full-host-smoke.json','components.json','legacy-components.json','restore.json','source-audit.json')]
    runtime=runtime_manifest(); verification=verification_manifest()
    checks={key:True for key in smoke.get('passed_cases',[]) if smoke.get('smoke_passed') is True}
    for key in ('tool','command','hook','single_writer','shutdown'):
        checks[key]=components.get('passed') is True and components.get(key,{}).get('passed') is True
    checks['restore']=restore.get('passed') is True
    fingerprint=(smoke.get('candidate_files')==runtime and components.get('candidate_files')==runtime and
        legacy.get('candidate_files')==runtime and restore.get('runtime_files')==runtime and audit.get('runtime_files')==runtime and
        offline.get('runtime_files')==runtime and offline.get('verification_files')==verification and
        audit.get('verification_files')==verification)
    ready=(fingerprint and offline.get('passed') is True and audit.get('passed') is True and legacy.get('passed') is True
        and restore.get('native_legacy_and_life') is True and smoke.get('host_commit')==HOST_COMMIT and smoke.get('sdk')==SDK_VERSION
        and smoke.get('real_model_requests',0)>=1 and len(checks)==11 and all(checks.values()))
    result={'integration_ready':ready,'passed':ready,'host_commit':HOST_COMMIT,'sdk':SDK_VERSION,
        'complete_host_started':smoke.get('complete_host_started') is True,'real_model_requests':smoke.get('real_model_requests',0),
        'checks':checks,'passed_cases':sorted(key for key,value in checks.items() if value),
        'runtime_files':runtime,'source_fingerprints_match':fingerprint,
        'static_unit_runner':offline.get('passed') is True,'public_source_and_export_audit':audit.get('passed') is True,
        'native_engine_roundtrip':restore.get('native_legacy_and_life') is True,
        'h01':'unsupported_no_send_exception','h02':'disabled_no_owned_frequency','h03':'disabled_no_trusted_identity',
        'contract_version':'1.0.0','long_observation':'deferred_to_joint_core_plugin_testing',
        'real_ecosystem':'not_run_deferred_to_joint_testing','production_switch':False,'independent_feature_development':'frozen'}
    (directory/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    public={'status':'integration_ready' if ready else 'not_ready','plugin_version':'0.3.1',
        'host_commit':HOST_COMMIT,'sdk':SDK_VERSION,'config_version':'3','schema_version':4,'contract_version':'1.0.0',
        'checks':checks,'source_fingerprints_match':fingerprint,
        'runtime_manifest_sha256':hashlib.sha256(json.dumps(runtime,sort_keys=True).encode()).hexdigest(),
        'static_unit_runner':result['static_unit_runner'],'public_source_and_export_audit':result['public_source_and_export_audit'],
        'native_engine_roundtrip':result['native_engine_roundtrip'],'real_model_requests_final_case':result['real_model_requests'],
        'long_observation':'joint_core_plugin_testing','real_ecosystem':'not_run_joint_testing',
        'h01':result['h01'],'h02':result['h02'],'h03':result['h03'],
        'production_switch':False,'independent_feature_development':'frozen'}
    (root/'docs/integration-ready.json').write_text(json.dumps(public,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='runtime_files'},indent=2))
    return result


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path); args=parser.parse_args()
    acceptance=evaluate()
    if not acceptance['integration_ready']: raise SystemExit('Integration Ready NOT passed; no ZIP')
    if args.output:
        checksum=build_zip(args.output.resolve(),acceptance)
        with zipfile.ZipFile(args.output) as archive:
            assert archive.testzip() is None
            actual={name.split('/',1)[1]:archive.read(name) for name in archive.namelist()}
            assert {name:data for name,data in actual.items() if name!='FILES.sha256.json'}==entries()
            assert json.loads(actual['FILES.sha256.json'])==file_manifest()
            with tempfile.TemporaryDirectory(prefix='ready-package-',dir=root/'.integration') as temporary:
                destination=Path(temporary).resolve()
                for name,data in actual.items():
                    path=destination/name; assert path.resolve().is_relative_to(destination)
                    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
                code="import importlib.util,sys,pathlib; p=pathlib.Path(sys.argv[1]);s=importlib.util.spec_from_file_location('ready_pkg',p/'__init__.py',submodule_search_locations=[str(p)]);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);from ready_pkg.plugin import create_plugin;q=create_plugin();assert q.get_components();assert q.config.plugin.config_version=='3' and q.config.plugin.enabled;assert m.__version__=='0.3.1'"
                subprocess.run([sys.executable,'-I','-c',code,str(destination)],cwd=destination,check=True,timeout=20)
        verification={'passed':True,'artifact':args.output.name,'sha256':checksum,'files':len(actual),
            'bytes':args.output.stat().st_size,'exact_source_match':True,'fresh_process_import':True,'default_enabled':True,
            'runtime_data_or_credentials':False}
        (root/'evidence/integration-ready/package.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
        print(json.dumps(verification,indent=2))


if __name__=='__main__': main()
