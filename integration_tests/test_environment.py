"""Explicit I0 suite; prepare a separate native Host environment and pinned source first."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import tempfile
import unittest

from .guard import child_environment
from .launcher import run_i0
from .process_job import ProcessJob
from .source import HOST,ROOT,verify_source


class I0EnvironmentTests(unittest.TestCase):
    def test_real_runner_contracts(self):
        report=run_i0()
        type(self).runner_report=report
        self.assertEqual(len(report['steps']),12)
        for step in report['steps']:
            with self.subTest(case=step['id']): self.assertTrue(step['passed'])
        self.assertTrue(report['temporary_instance_removed'])
        self.assertTrue(report['source_after_verified'])
        self.assertFalse(report['formal_R0_acceptance'])
        self.assertEqual(report['real_provider_requests'],0)
        self.assertEqual(report['lifecycle'],{'loads':2,'unloads':2})

    def test_environment_does_not_inherit_credentials_or_compatibility_overrides(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{'MAIBOT_SESSION_TOKEN':'secret','OPENAI_API_KEY':'secret',
                                   'HTTPS_PROXY':'secret','MAIBOT_FORCE_SDK_COMPAT':'1','PYTHONPATH':'unsafe'}):
            env=child_environment(Path(tempfile.gettempdir()),HOST,ROOT)
        self.assertTrue({'MAIBOT_SESSION_TOKEN','OPENAI_API_KEY','HTTPS_PROXY','MAIBOT_FORCE_SDK_COMPAT'}.isdisjoint(env))
        self.assertNotIn('unsafe',env['PYTHONPATH'])
        self.assertEqual(verify_source(),188)

    def test_guard_denies_non_source_read(self):
        with tempfile.TemporaryDirectory(prefix='maivita-i0-guard-') as temp:
            work=Path(temp)
            code="""
from pathlib import Path
import os
from integration_tests.guard import install_guard
from integration_tests.source import ROOT,HOST
install_guard(Path(os.environ['MAIBOT_INSTANCE_DIR']),HOST,ROOT)
try: (ROOT/'config.example.toml').read_bytes()
except PermissionError: pass
else: raise AssertionError('non-source repository read allowed')
"""
            process=subprocess.run([sys.executable,'-c',code],cwd=work,env=child_environment(work,HOST,ROOT),
                capture_output=True,timeout=10)
            self.assertEqual(process.returncode,0,process.stderr.decode('utf-8',errors='replace'))

    def test_job_closes_descendant_process_on_failure(self):
        # Hold the parent at stdin until assigned, then create a harmless child.
        code="import subprocess,sys,time; sys.stdin.buffer.read(1); p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
        process=None; child_handle=None
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; api.OpenProcess.restype=wintypes.HANDLE
        api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        try:
            with ProcessJob() as job:
                process=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
                job.assign(process)
                process.stdin.write(b'1'); process.stdin.flush()
                child_pid=int(process.stdout.readline())
                child_handle=api.OpenProcess(0x100000,False,child_pid)  # SYNCHRONIZE only
                self.assertTrue(child_handle)
                # Simulate coordinator failure: no graceful shutdown request.
            process.wait(timeout=5)
            self.assertEqual(api.WaitForSingleObject(child_handle,5000),0)
        finally:
            if child_handle: api.CloseHandle(child_handle)
            if process is not None:
                if process.poll() is None: process.kill(); process.wait()
                process.stdin.close(); process.stdout.close()

    def test_launcher_timeout_removes_temporary_instance(self):
        before=set(Path(tempfile.gettempdir()).glob('maivita-i0-*'))
        with self.assertRaises(subprocess.TimeoutExpired):
            run_i0(timeout=.001)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob('maivita-i0-*')),before)

    def test_source_verifier_rejects_tampering_and_unsafe_paths(self):
        from unittest.mock import patch
        from . import source
        with tempfile.TemporaryDirectory(prefix='maivita-source-check-') as temp:
            work=Path(temp).resolve(); host=work/'host'; host.mkdir()
            sample=host/'sample.py'; sample.write_bytes(b'# original\n')
            lock=work/'lock.json'
            manifest={'commit':source.COMMIT,'archive_sha256':source.ARCHIVE_SHA256,
                      'extracted_files':[{'path':'sample.py','sha256':source.file_digest(sample)}]}
            lock.write_text(json.dumps(manifest),encoding='utf-8')
            with patch.object(source,'HOST',host),patch.object(source,'LOCK',lock):
                self.assertEqual(source.verify_source(),1)
                sample.write_bytes(b'# changed\n')
                with self.assertRaisesRegex(ValueError,'changed'): source.verify_source()
                sample.write_bytes(b'# original\n')
                extra=host/'extra.py'; extra.write_bytes(b'# extra\n')
                with self.assertRaisesRegex(ValueError,'unexpected'): source.verify_source()
                extra.unlink()
                for bad in ('../escape.py','C:/escape.py','/absolute.py','nested\\escape.py'):
                    with self.subTest(path=bad):
                        manifest['extracted_files'][0]['path']=bad
                        lock.write_text(json.dumps(manifest),encoding='utf-8')
                        with self.assertRaisesRegex(ValueError,'unsafe'): source.locked_files()


if __name__=='__main__': unittest.main()
