import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_model_selection_capabilities_are_declared(self):
        manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
        capabilities = set(manifest.get("capabilities", []))
        self.assertIn("llm.generate", capabilities)
        self.assertNotIn("llm.get_available_models", capabilities)
        self.assertNotIn("frequency.set_adjust", capabilities)
        self.assertIn("frequency.get_adjust", capabilities)
        self.assertIn("frequency.get_current_talk_value", capabilities)
        self.assertEqual(manifest["version"], "0.5.2")


if __name__ == "__main__":
    unittest.main()
