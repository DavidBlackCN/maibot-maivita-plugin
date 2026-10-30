"""Run native Host/Runner IPC contracts using a disposable local instance."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import importlib.metadata
import json
import platform
import sys
import unittest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from tools.workspace_paths import EVIDENCE
from integration_tests.launcher import run_i0

if __name__=='__main__':
    (EVIDENCE/'i0').mkdir(parents=True,exist_ok=True)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-environment',action='store_true',help='also test environment filtering, read guard and forced process-tree cleanup')
    args=parser.parse_args()
    if args.verify_environment:
        from integration_tests.test_environment import I0EnvironmentTests
        with (EVIDENCE/'i0/environment-tests.txt').open('w',encoding='utf-8') as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(I0EnvironmentTests))
        summary={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'runner_cases':12}
        (EVIDENCE/'i0/environment-tests.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        report=getattr(I0EnvironmentTests,'runner_report',{'passed':False})
        report['environment_suite']=summary
        if not result.wasSuccessful(): report['passed']=False
    else:
        try: report=run_i0()
        except Exception as error:
            (EVIDENCE/'i0/results.json').write_text(json.dumps({'passed':False,'error':str(error)},indent=2),encoding='utf-8')
            raise
    report['recorded_at']=datetime.now(timezone.utc).isoformat()
    report['environment']={'python':platform.python_version(),'platform':platform.platform(),
        'packages':{dist.metadata['Name']:dist.version for dist in importlib.metadata.distributions()}}
    (EVIDENCE/'i0/results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    raise SystemExit(not report['passed'])
