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
        self.assertNotIn("frequency.get_adjust", capabilities)
        self.assertNotIn("frequency.get_current_talk_value", capabilities)
        self.assertEqual(capabilities, {'message.get_by_id', 'api.call', 'send.text', 'config.get', 'llm.generate',
                                        'chat.get_all_streams', 'component.list_loaded_plugins'})
        self.assertEqual(manifest["version"], "0.9.0")


if __name__ == "__main__":
    unittest.main()
