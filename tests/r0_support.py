"""Load hyphenated distribution and source-execute fixed Host contracts offline."""
import ast
import os
os.environ.setdefault('MAIBOT_HOST_VERSION','1.3.12')  # isolated Host fixture
from dataclasses import dataclass, field
import logging
from pathlib import Path
import sys
import types
import typing

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault(package.__name__, package)


def host_classes(relative_path, names):
    """Run actual class bodies; imports/network/host startup are excluded.

    Infrastructure timeout/logging dependencies are supplied by the fixture.
    Registry/resolution/merge logic itself is the unmodified downloaded source.
    """
    path = ROOT / "tests/fixtures/host_contracts" / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names]
    module = types.ModuleType("_host_contract_" + path.stem)
    sys.modules[module.__name__] = module
    module.__dict__.update(vars(typing))
    module.__dict__.update(dataclass=dataclass, field=field, logger=logging.getLogger("host_contract"),
                           normalize_component_timeout_ms=lambda value: int(value or 0),
                           resolve_component_rpc_timeout_ms=lambda value: int(value or 5000))
    selected = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *classes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), str(path), "exec"), module.__dict__)
    return module
