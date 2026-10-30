"""Keep generated reports outside the plugin checkout, including standalone CI."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path(os.environ.get('MAIBOT_EVIDENCE_DIR', ROOT.parent / 'docs' / 'reports' / ROOT.name)).resolve()
if EVIDENCE.is_relative_to(ROOT):
    raise ValueError('MAIBOT_EVIDENCE_DIR must be outside the plugin repository')
