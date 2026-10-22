"""R0 preparation only. No activation, source mutation or new-schema import."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maivita.life.storage.safety import discover, inspect_database, snapshot, restore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["discover", "backup", "dry-run", "restore"])
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--legacy-root", type=Path, action="append", default=[],
                        help="explicit old CODE directory; discover data/goals.db")
    parser.add_argument("--legacy-data-dir", type=Path, action="append", default=[],
                        help="explicit verified old SDK DATA directory; discover goals.db")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--offline", action="store_true",
                        help="assert source writer stopped/checkpointed; refuses any existing WAL")
    args = parser.parse_args(argv)
    report = {"tool_version": "0.1.0", "stage": "R0", "run_id": uuid4().hex,
              "activated": False, "new_schema_import": "not_implemented_R1"}
    try:
        candidates = args.source or ([p / "goals.db" for p in args.legacy_data_dir] +
                                     [p / "data" / "goals.db" for p in args.legacy_root])
        report.update(discover(candidates))
        if report["status"] == "ambiguous":
            raise ValueError("multiple candidates; explicitly choose exactly one source")
        if args.action != "discover":
            if not report["selected"]:
                raise ValueError("no source database found")
            source = Path(report["selected"])
            report["preflight"] = inspect_database(source, offline=args.offline)
            if not report["preflight"]["valid_legacy_schema"]:
                raise ValueError("source schema/integrity not verified")
            if not args.output:
                raise ValueError("--output required (new snapshot file outside source directory)")
            report.update(restore(source, args.output, args.sha256 or "") if args.action == "restore" else snapshot(source, args.output, offline=args.offline))
            if args.action == "dry-run":
                report["status"] = "dry_run_prepared"
                report["limitations"] = ["R0 snapshot only; row classification and new schema require R1",
                                          "live snapshot is not a final cutover snapshot"]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        report.update(status="failed", error=str(exc))
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
