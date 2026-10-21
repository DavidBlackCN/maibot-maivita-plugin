"""Run all regressions with UTF-8 logs and a machine-readable result."""
from datetime import datetime, timezone
import argparse
import importlib.metadata
import json
import logging
from pathlib import Path
import platform
import sys
import unittest

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tests"))
import r0_support


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-prefix", default="test-results")
    args = parser.parse_args()
    if not args.report_prefix or any(not (c.isalnum() or c in "-_") for c in args.report_prefix):
        parser.error("report-prefix must be a simple file stem")
    (root / "evidence").mkdir(exist_ok=True)
    log = root / "evidence" / (args.report_prefix + ".txt")
    with log.open("w", encoding="utf-8") as stream:
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.discover(str(root / "tests")))
        logging.getLogger().removeHandler(handler)
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "platform": platform.platform(), "sdk": importlib.metadata.version("maibot-plugin-sdk"),
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "passed": result.wasSuccessful(),
              "real_host_runtime": "not_run", "production_data": "not_accessed"}
    (root / "evidence" / (args.report_prefix + ".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return not result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
