"""Load the repository as a package despite its distribution directory containing hyphens."""

from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "maibot_autonomous_planning_plugin"
module = types.ModuleType(PACKAGE)
module.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, module)
