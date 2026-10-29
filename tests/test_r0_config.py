"""Preserve original attribution and archived provenance bytes."""
import json
import unittest
from r0_support import ROOT


class ConfigTests(unittest.TestCase):
    def test_T01_original_license_and_legacy_entry_preserved(self):
        import hashlib
        baseline = json.loads((ROOT / "tests/fixtures/preserved-files.json").read_text(encoding="utf-8"))
        for name in ("LICENSE", "tests/fixtures/provenance/legacy_plugin.r0.py"):
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), baseline[name])
        self.assertFalse((ROOT / "data").exists())
