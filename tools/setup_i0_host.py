"""Fetch only hash-locked public source files; never run or modify a Host."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from integration_tests.source import COMMIT,setup_source

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    print({'commit':COMMIT,'verified_files':setup_source(offline=args.offline),'host_started':False})
