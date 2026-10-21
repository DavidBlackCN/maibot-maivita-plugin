"""Create and destroy a complete I0 instance; no user-supplied data/endpoint."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .guard import child_environment
from .process_job import ProcessJob
from .source import HOST,ROOT,verify_source


def run_i0(*,timeout=60):
    count=verify_source()
    with tempfile.TemporaryDirectory(prefix='maivita-i0-') as temp:
        work=Path(temp).resolve()
        plugins=work/'plugins'; plugins.mkdir()
        for name,plugin_id,source,capabilities in [
            ('probe','maivita.i0.probe','probe_plugin.py',['llm.generate','send.text','frequency.set_adjust']),
            ('consumer','maivita.i0.consumer','consumer_plugin.py',['api.call','database.query'])]:
            directory=plugins/name; directory.mkdir()
            manifest={'manifest_version':2,'id':plugin_id,'name':'MaiVita I0 '+name,'version':'0.0.1',
                'description':'Temporary integration fixture, never install in production',
                'author':{'name':'MaiVita I0 tests','url':'https://github.com/DavidBlackCN'},
                'urls':{'repository':'https://github.com/DavidBlackCN/maibot-maivita-plugin'},
                'i18n':{'default_locale':'zh-CN','supported_locales':['zh-CN']},
                'license':'AGPL-3.0','plugin_type':'extension',
                'host_application':{'min_version':'1.3.12','max_version':'1.3.12'},
                'sdk':{'min_version':'2.15.0','max_version':'2.15.0'},'capabilities':capabilities,'dependencies':[]}
            (directory/'_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            shutil.copyfile(ROOT/'integration_tests/fixtures'/source,directory/'plugin.py')
        env=child_environment(work,HOST,ROOT,MAIVITA_I0_START_GATE='1')
        # The scenario cannot spawn a Runner until it receives this gate byte.
        # Assign the whole tree to the job before releasing that gate.
        with ProcessJob() as job:
            process=subprocess.Popen([sys.executable,'-m','integration_tests.scenario'],cwd=work,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            try:
                job.assign(process)
                output=process.communicate(input=b'1',timeout=timeout)[0].decode('utf-8',errors='replace')
            finally:
                job.close()  # also kills any orphaned Runner on failure/timeout
                if process.poll() is None: process.kill()
                process.wait()
                for stream in (process.stdin,process.stdout):
                    if stream is not None: stream.close()
        if process.returncode:
            raise RuntimeError('I0 isolated scenario failed:\n'+output[-11000:])
        report=json.loads((work/'i0-result.json').read_text(encoding='utf-8'))
        report.update(host_source_files_verified=count,temporary_instance_removed=True,
            source_after_verified=verify_source()==count,process_tree_containment='Windows kill-on-close job, assigned before spawn gate',
            environment_policy='allowlisted OS env only; no deployment/model credentials inherited')
    return report
