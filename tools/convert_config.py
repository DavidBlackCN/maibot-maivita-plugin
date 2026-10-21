"""Convert an explicit configuration COPY. Never reads a default production path."""
from pathlib import Path
import argparse
import json
import sys
import tomllib
import types
import tomli_w

root = Path(__file__).resolve().parents[1]
package = types.ModuleType("maivita")
package.__path__ = [str(root)]
sys.modules.setdefault("maivita", package)
from maivita.compatibility.config import convert_legacy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        with args.source.open("rb") as file:
            converted, report = convert_legacy(tomllib.load(file))
    except Exception as exc:
        # Validation exception reprs can contain full configuration values.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "reason": "invalid/unverified input; source unchanged"}))
        return 2
    if converted is None:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    try:
        with args.output.open("xb") as file:
            tomli_w.dump(converted, file)
    except OSError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "reason": "output must be a new writable file; source unchanged"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
