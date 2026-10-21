"""Start the complete original Host CLI only after process-job assignment."""
import os
import runpy
import sys

if os.environ.get('MAIVITA_I1_START_GATE')!='1' or sys.stdin.buffer.read(1)!=b'1':
    raise SystemExit('I1 start gate not released')
sys.argv=['maibot','run','--directory',os.environ['MAIBOT_INSTANCE_DIR']]
runpy.run_module('src.cli.tool',run_name='__main__')
