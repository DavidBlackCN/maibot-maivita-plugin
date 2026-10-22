import ast
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import tomllib
import unittest
from r0_support import ROOT

from maibot_autonomous_planning_plugin.maivita.compatibility.config import convert_legacy
from maibot_autonomous_planning_plugin.maivita.compatibility.legacy_config import AutonomousPlanningV4Config as LegacyConfig
from maibot_autonomous_planning_plugin.maivita.legacy.config_models import MaiVitaConfig
from maibot_autonomous_planning_plugin.maivita.legacy.services.command_service import CommandService
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_T01_full_v51_projection_preserves_all_fields_and_reports_permissions(self):
        old = LegacyConfig().model_dump()
        old["schedule"].update(no_sleep_mode=True, admin_users=[], timezone="Asia/Tokyo",
                               min_activities=7, max_activities=20)
        old["schedule"]["model"].update(mode="model", model="synthetic-model")
        before = deepcopy(old)
        converted, report = convert_legacy(old)
        self.assertEqual(old, before)
        projected = MaiVitaConfig.model_validate(converted)
        self.assertEqual(projected.schedule.model_dump(), LegacyConfig.model_validate(old).schedule.model_dump())
        self.assertEqual(projected.inject.model_dump(), old["inject"])
        self.assertEqual(projected.autonomous_planning.model_dump(), old["autonomous_planning"])
        self.assertEqual(projected.sleep.mode, "disabled")
        self.assertEqual(projected.planning.mode, "legacy")
        self.assertTrue(report["diagnostics"])
        self.assertTrue(CommandService(NS(config=projected))._check_permission("ordinary-user"))

    def test_T05_fresh_config_denies_empty_admin_legacy_requires_explicit_conversion(self):
        cfg = MaiVitaConfig()
        self.assertFalse(CommandService(NS(config=cfg))._check_permission("ordinary-user"))
        cfg.admin.legacy_admin_users = ["trusted-user"]
        svc = CommandService(NS(config=cfg))
        self.assertTrue(svc._check_permission("trusted-user"))
        self.assertFalse(svc._check_permission("ordinary-user"))

    def test_T01_unknown_removed_fields_report_paths_without_secret_values(self):
        old = LegacyConfig().model_dump()
        old["schedule"]["llm_task_name"] = "synthetic-secret-never-in-report"
        converted, report = convert_legacy(old)
        self.assertIsNone(converted)
        self.assertIn("schedule.llm_task_name", report["unknown_paths"])
        self.assertNotIn("synthetic-secret", json.dumps(report))
        old["plugin"]["config_version"] = "unknown"
        with self.assertRaises(ValueError):
            convert_legacy(old)

    def test_T01_conversion_CLI_creates_new_config_and_never_overwrites(self):
        source = ROOT / "tests/fixtures/config.v5.1.0.example.toml"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "converted.toml"
            command = [sys.executable, str(ROOT / "tools" / "convert_config.py"), str(source), str(output)]
            result = subprocess.run(command, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)["status"], "converted")
            with output.open("rb") as file:
                cfg = MaiVitaConfig.model_validate(tomllib.load(file))
            self.assertEqual(cfg.plugin.config_version, "1")
            self.assertTrue(cfg.compatibility.legacy_empty_admin_all)
            saved = output.read_bytes()
            second = subprocess.run(command, capture_output=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(json.loads(second.stdout)["status"], "failed")
            self.assertEqual(output.read_bytes(), saved)
        self.assertEqual(source.read_bytes(), before)

    def test_modes_and_timezone_validation_do_not_enable_R2(self):
        for data in [{"planning": {"mode": "autonomous"}}, {"sleep": {"mode": "autonomous"}},
                     {"character": {"timezone": "Not/A_Zone"}}, {"private_secret": "reject"}]:
            with self.subTest(data=data), self.assertRaises(Exception):
                MaiVitaConfig.model_validate(data)

    def test_T01_original_license_and_legacy_entry_preserved(self):
        import hashlib
        baseline = json.loads((ROOT / "tests/fixtures/preserved-files.json").read_text(encoding="utf-8"))
        for name in ("LICENSE", "tests/fixtures/provenance/legacy_plugin.r0.py"):
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), baseline[name])
        self.assertFalse((ROOT / "data").exists())

    def test_T04_all_legacy_public_parameters_remain(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "legacy_entrypoints.json").read_text(encoding="utf-8"))
        module = ast.parse((ROOT / "maivita/legacy/plugin.py").read_text(encoding="utf-8"))
        methods = {method.name: method for cls in module.body if isinstance(cls, ast.ClassDef)
                   for method in cls.body if isinstance(method, ast.AsyncFunctionDef)}
        for entry in fixture:
            if entry["kind"] not in {"Tool", "API", "Command"}:
                continue
            method = methods[entry["handler"]]
            decorator = next(d for d in method.decorator_list if isinstance(d, ast.Call) and d.func.id == entry["kind"])
            self.assertEqual(ast.literal_eval(decorator.args[0]), entry["name"])
            if entry["parameters"]:
                params = next(k.value for k in decorator.keywords if k.arg == "parameters")
                actual = [{kw.arg: ast.unparse(kw.value) for kw in p.keywords} for p in params.elts]
                self.assertEqual(actual, entry["parameters"])


if __name__ == "__main__":
    unittest.main()
