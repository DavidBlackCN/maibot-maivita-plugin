import unittest
from datetime import datetime
from pathlib import Path
import sys
import types
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("maibot_autonomous_planning_plugin", package)

from maibot_autonomous_planning_plugin.maivita.legacy.core.activity_state import (
    PUBLIC_SLEEP_DESCRIPTION,
    PUBLIC_SLEEP_NAME,
    is_locked_sleep,
    public_activity_text,
    public_error_text,
)
from maibot_autonomous_planning_plugin.maivita.legacy.planner.generator.validator import ScheduleSemanticValidator
from maibot_autonomous_planning_plugin.maivita.legacy.planner.generator.config import ScheduleGeneratorConfig
from maibot_autonomous_planning_plugin.maivita.legacy.planner.generator.prompt_builder import PromptBuilder
from maibot_autonomous_planning_plugin.maivita.legacy.planner.schedule_constraints import normalize_daily_schedule
from maibot_autonomous_planning_plugin.maivita.shared.utils.time_utils import TimeParseError, TimeRange, parse_hhmm


class TimeConstraintTests(unittest.TestCase):
    def test_sleep_windows(self):
        for start, end, duration in [
            ("01:00", "08:00", 420), ("23:00", "07:00", 480),
            ("00:00", "08:00", 480), ("23:30", "24:00", 30),
        ]:
            with self.subTest(start=start, end=end):
                self.assertEqual(TimeRange.from_strings(start, end).duration_minutes, duration)

    def test_invalid_time_never_falls_back_to_midnight(self):
        for value in ["24:30", "25:00", "12:99", "abc", ""]:
            with self.subTest(value=value), self.assertRaises(TimeParseError):
                parse_hhmm(value, allow_24=True)

    def test_cross_midnight_overlap_detects_early_morning(self):
        self.assertTrue(TimeRange.from_strings("23:00", "07:00").overlaps(
            TimeRange.from_strings("01:00", "02:00")
        ))

    def test_llm_conflicts_become_one_locked_sleep(self):
        raw = [
            {"name": "睡觉", "description": "抱着柔软的枕头沉入安静梦乡。", "goal_type": "daily_routine", "priority": "high", "time_slot": "00:00", "duration_hours": 7},
            {"name": "起床", "description": "", "goal_type": "daily_routine", "priority": "medium", "time_slot": "07:00", "duration_hours": 1},
            {"name": "早餐", "description": "", "goal_type": "meal", "priority": "medium", "time_slot": "08:00", "duration_hours": 0.5},
        ]
        sleep = TimeRange.from_strings("01:00", "08:00")
        normalized = normalize_daily_schedule(raw, sleep_window=sleep, no_sleep_mode=False)
        locked = [item for item in normalized.items if is_locked_sleep(item.get("parameters"))]
        self.assertEqual(len(locked), 1)
        self.assertEqual((locked[0]["time_slot"], locked[0]["duration_hours"]), ("01:00", 7))
        self.assertEqual(locked[0]["name"], PUBLIC_SLEEP_NAME)
        self.assertEqual(locked[0]["description"], "抱着柔软的枕头沉入安静梦乡。")
        self.assertFalse(normalized.errors)
        visible = f"{locked[0]['name']} {locked[0]['description']}".lower()
        for forbidden in ("系统锁定", "固定睡眠块", "硬约束", "system activity", "locked"):
            self.assertNotIn(forbidden, visible)
        self.assertEqual([item["name"] for item in normalized.items], ["睡觉", "早餐"])
        self.assertTrue(locked[0]["parameters"]["locked"])
        self.assertTrue(locked[0]["parameters"]["system_activity"])
        self.assertTrue(ScheduleSemanticValidator().validate(normalized.items, sleep_window=sleep).valid)

    def test_legacy_locked_sleep_text_is_sanitized_for_display(self):
        params = {
            "activity_state": "sleeping",
            "system_activity": True,
            "locked": True,
        }
        name, description = public_activity_text(
            "固定睡眠块", "系统锁定的固定睡眠时间（硬约束 / locked）", params
        )
        self.assertEqual((name, description), (PUBLIC_SLEEP_NAME, PUBLIC_SLEEP_DESCRIPTION))
        self.assertEqual(
            public_error_text("固定睡眠块缺失（hard constraint / locked）"),
            "日程处理未完成，请查看日志",
        )

    def test_no_sleep_mode(self):
        raw = [{"name": "睡觉", "description": "", "goal_type": "daily_routine", "priority": "high", "time_slot": "01:00", "duration_hours": 7}]
        normalized = normalize_daily_schedule(raw, sleep_window=None, no_sleep_mode=True)
        self.assertEqual(normalized.items[0]["goal_type"], "free_time")
        self.assertFalse(any(is_locked_sleep(item.get("parameters")) for item in normalized.items))
        self.assertTrue(ScheduleSemanticValidator().validate(normalized.items, no_sleep_mode=True).valid)

    def test_no_sleep_ignores_sleep_values(self):
        disabled = ScheduleGeneratorConfig({
            "no_sleep_mode": True, "sleep_start_time": "bad", "sleep_end_time": "also-bad"
        })
        self.assertIsNone(disabled.sleep_window)

    def test_sleep_description_is_required_and_internal_terms_are_rejected(self):
        sleep = TimeRange.from_strings("01:00", "08:00")
        missing = normalize_daily_schedule([], sleep_window=sleep, no_sleep_mode=False)
        self.assertTrue(missing.errors)
        unsafe = normalize_daily_schedule([
            {"name": "睡觉", "description": "系统锁定的睡眠块", "goal_type": "daily_routine",
             "priority": "high", "time_slot": "01:00", "duration_hours": 7}
        ], sleep_window=sleep, no_sleep_mode=False)
        self.assertTrue(unsafe.errors)
        locked = next(item for item in unsafe.items if is_locked_sleep(item.get("parameters")))
        self.assertEqual(locked["description"], "")

    def test_sleep_description_remains_required_when_detailed_descriptions_are_disabled(self):
        prompt = PromptBuilder(
            {
                "enable_detailed_description": False,
                "sleep_start_time": "01:00",
                "sleep_end_time": "08:00",
                "bot_profile": {},
            },
            SimpleNamespace(get_now=lambda: datetime(2026, 9, 6, 0, 0)),
        ).build_schedule_prompt("daily", {})
        self.assertIn("除“睡觉”外的活动 description 填空字符串", prompt)
        self.assertIn("“睡觉”仍必须填写", prompt)

    def test_error_blocks_warning_does_not(self):
        validator = ScheduleSemanticValidator()
        invalid = validator.validate([
            {"name": "坏时间", "goal_type": "custom", "priority": "low", "time_slot": "abc", "duration_hours": 1}
        ], no_sleep_mode=True)
        self.assertFalse(invalid.valid)
        self.assertTrue(invalid.errors)
        warning_only = validator.validate([
            {"name": "午餐", "goal_type": "meal", "priority": "medium", "time_slot": "16:00", "duration_hours": 0.5}
        ], no_sleep_mode=True)
        self.assertTrue(warning_only.valid)
        self.assertTrue(warning_only.warnings)


if __name__ == "__main__":
    unittest.main()
