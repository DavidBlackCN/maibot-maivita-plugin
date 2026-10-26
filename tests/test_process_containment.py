"""Linux I1 cleanup kills the isolated Host's descendants on failure."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import r0_support
from maibot_autonomous_planning_plugin.integration_tests.process_job import ProcessJob


@unittest.skipIf(os.name=='nt','Windows descendant cleanup is covered by I0 environment tests')
class LinuxProcessContainmentTests(unittest.TestCase):
    def test_descendants_stop_when_job_closes(self):
        code="import subprocess,sys,time; sys.stdin.buffer.read(1); child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(child.pid,flush=True); time.sleep(60)"
        process=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,start_new_session=True)
        try:
            with ProcessJob() as job:
                job.assign(process)
                process.stdin.write(b'1'); process.stdin.flush()
                child=int(process.stdout.readline())
            process.wait(timeout=5)
            state=Path('/proc')/str(child)/'stat'
            deadline=time.monotonic()+5
            while state.exists() and state.read_text().split()[2]!='Z':
                self.assertLess(time.monotonic(),deadline,'descendant survived process containment')
                time.sleep(.02)
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdin.close(); process.stdout.close()
