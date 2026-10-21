import unittest
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("maibot_autonomous_planning_plugin", package)

from maibot_autonomous_planning_plugin.planner.generator.response_parser import LLMResponseParser


class ResponseParserTests(unittest.TestCase):
    def test_extracts_fenced_schedule_json_after_reasoning_text(self):
        response = "先分析作息。\n```json\n{\"schedule_items\":[{\"name\":\"睡觉\"}]}\n```\n完成。"
        self.assertEqual(
            LLMResponseParser.parse_schedule_response(response),
            [{"name": "睡觉"}],
        )

    def test_extracts_unfenced_json_with_surrounding_text_and_bom(self):
        response = "\ufeff分析：需要自然安排。\n{\"schedule_items\": [{\"name\": \"学习\"}]}\n以上。"
        self.assertEqual(
            LLMResponseParser.parse_schedule_response(response),
            [{"name": "学习"}],
        )

    def test_empty_response_has_explicit_error(self):
        with self.assertRaisesRegex(Exception, "LLM response is empty"):
            LLMResponseParser.parse_schedule_response("")


if __name__ == "__main__":
    unittest.main()
