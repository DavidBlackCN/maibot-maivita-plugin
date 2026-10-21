import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("maibot_autonomous_planning_plugin", package)

from maibot_autonomous_planning_plugin.config_models import ScheduleFrequencyConfig
from maibot_autonomous_planning_plugin.core.frequency import (
    base_frequency_at,
    rule_boundaries,
)
from maibot_autonomous_planning_plugin.services.command_service import CommandService


class FrequencyRuleTests(unittest.TestCase):
    def test_rule_priority_and_cross_midnight(self):
        stream = {"platform": "qq", "group_id": "42", "is_group_session": True}
        rules = [
            {"rule_type": "group", "platform": "", "item_id": "", "time": "", "value": 0.8},
            {"rule_type": "group", "platform": "qq", "item_id": "42", "time": "23:00-06:59", "value": 0.4},
            {"rule_type": "group", "platform": "qq", "item_id": "*", "time": "*", "value": 0.9},
        ]
        self.assertEqual(base_frequency_at(1.0, rules, minute=60, stream=stream, rules_enabled=True), 0.4)
        self.assertEqual(base_frequency_at(1.0, rules, minute=720, stream=stream, rules_enabled=True), 0.9)
        self.assertIn(420, rule_boundaries(rules))

    def test_frequency_config_rejects_negative_and_non_finite(self):
        for value in (-0.1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ScheduleFrequencyConfig(sleep_factor=value)
            with self.subTest(activity_value=value), self.assertRaises(ValueError):
                ScheduleFrequencyConfig(activity_factors={"custom": value})

    def test_all_default_activity_factors(self):
        factors = ScheduleFrequencyConfig().activity_factors
        self.assertEqual(
            factors.model_dump(),
            {
                "daily_routine": 1.0,
                "meal": 1.2,
                "study": 0.3,
                "entertainment": 1.5,
                "social_maintenance": 1.5,
                "exercise": 0.7,
                "learn_topic": 0.5,
                "rest": 1.3,
                "free_time": 1.3,
                "custom": 1.0,
            },
        )

    def test_private_rule_does_not_match_group_and_exact_target_wins(self):
        private_stream = {"platform": "qq", "user_id": "7", "is_group_session": False}
        group_stream = {"platform": "qq", "group_id": "7", "is_group_session": True}
        rules = [
            {"rule_type": "private", "platform": "", "item_id": "", "time": "", "value": 0.6},
            {"rule_type": "private", "platform": "*", "item_id": "*", "time": "*", "value": 0.7},
            {"rule_type": "private", "platform": "qq", "item_id": "7", "time": "10:00-11:00", "value": 0.2},
        ]
        self.assertEqual(
            base_frequency_at(1.0, rules, minute=630, stream=private_stream, rules_enabled=True),
            0.2,
        )
        self.assertEqual(
            base_frequency_at(1.0, rules, minute=630, stream=group_stream, rules_enabled=True),
            1.0,
        )


class FakeConfig:
    VALUES = {
        "chat.reply_timing.talk_value": 1.0,
        "chat.reply_timing.enable_talk_value_rules": True,
        "chat.reply_timing.talk_value_rules": [
            {"rule_type": "group", "platform": "qq", "item_id": "42", "time": "00:00-08:59", "value": 0.8},
            {"rule_type": "group", "platform": "qq", "item_id": "42", "time": "09:00-23:59", "value": 1.0},
        ],
    }

    async def get(self, key, default=None):
        return self.VALUES.get(key, default)


class FakeFrequency:
    async def get_adjust(self, _stream_id):
        return 1.5

    async def get_current_talk_value(self, _stream_id):
        return 1.5


class FakeChat:
    async def get_all_streams(self):
        return [{"session_id": "s", "platform": "qq", "group_id": "42", "is_group_session": True}]


class FrequencyCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_splits_schedule_at_maibot_rule_boundary(self):
        frequency_cfg = ScheduleFrequencyConfig()
        schedule_cfg = SimpleNamespace(
            frequency=frequency_cfg,
            no_sleep_mode=False,
            timezone="Asia/Shanghai",
            enable_detailed_description=True,
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(schedule=schedule_cfg),
            ctx=SimpleNamespace(config=FakeConfig(), frequency=FakeFrequency(), chat=FakeChat()),
        )
        service = CommandService(plugin)
        captured = []

        async def capture(_stream, text):
            captured.append(text)

        service._send_forward = capture
        service._make_tz_manager = lambda: SimpleNamespace(
            get_now=lambda: datetime(2026, 9, 6, 10, 0)
        )
        goal = SimpleNamespace(
            name="看电影",
            description="",
            goal_type="entertainment",
            parameters={"time_window": [480, 600]},
            conditions={},
        )
        manager = SimpleNamespace(get_schedule_goals=lambda **_kwargs: [goal])
        with patch(
            "maibot_autonomous_planning_plugin.services.command_service.get_goal_manager",
            return_value=manager,
        ):
            await service._handle_frequency("s", "qq", "42", "u")

        output = captured[0]
        self.assertIn("08:00-09:00", output)
        self.assertIn("09:00-10:00", output)
        self.assertIn("实际 1.200", output)
        self.assertIn("实际 1.500", output)
        self.assertIn("Focus Mode", output)

    async def test_command_reports_api_failure_without_fabricated_values(self):
        class FailingConfig:
            async def get(self, _key, _default=None):
                raise RuntimeError("capability unavailable")

        schedule_cfg = SimpleNamespace(
            frequency=ScheduleFrequencyConfig(),
            no_sleep_mode=False,
            timezone="Asia/Shanghai",
            enable_detailed_description=True,
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(schedule=schedule_cfg),
            ctx=SimpleNamespace(
                config=FailingConfig(), frequency=FakeFrequency(), chat=FakeChat()
            ),
        )
        service = CommandService(plugin)
        captured = []

        async def capture(_stream, text):
            captured.append(text)

        service._send = capture
        goal = SimpleNamespace(
            name="学习",
            description="",
            goal_type="study",
            parameters={"time_window": [480, 600]},
            conditions={},
        )
        manager = SimpleNamespace(get_schedule_goals=lambda **_kwargs: [goal])
        with patch(
            "maibot_autonomous_planning_plugin.services.command_service.get_goal_manager",
            return_value=manager,
        ):
            await service._handle_frequency("s", "qq", "42", "u")

        self.assertIn("无法读取 MaiBot 频率配置", captured[0])
        self.assertNotIn("实际 0.", captured[0])

    async def test_current_schedule_gap_still_checks_adjust_mismatch(self):
        frequency_cfg = ScheduleFrequencyConfig()
        schedule_cfg = SimpleNamespace(
            frequency=frequency_cfg,
            no_sleep_mode=False,
            timezone="Asia/Shanghai",
            enable_detailed_description=True,
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(schedule=schedule_cfg),
            ctx=SimpleNamespace(config=FakeConfig(), frequency=FakeFrequency(), chat=FakeChat()),
            is_sleeping_now=lambda: False,
        )
        service = CommandService(plugin)
        captured = []
        service._send_forward = lambda _stream, text: _capture_async(captured, text)
        service._make_tz_manager = lambda: SimpleNamespace(
            get_now=lambda: datetime(2026, 9, 6, 12, 0)
        )
        goal = SimpleNamespace(
            name="早餐",
            description="",
            goal_type="meal",
            parameters={"time_window": [480, 540]},
            conditions={},
        )
        manager = SimpleNamespace(get_schedule_goals=lambda **_kwargs: [goal])
        with patch(
            "maibot_autonomous_planning_plugin.services.command_service.get_goal_manager",
            return_value=manager,
        ):
            await service._handle_frequency("s", "qq", "42", "u")

        self.assertIn("可能被其他插件覆盖", captured[0])


async def _capture_async(target, value):
    target.append(value)


if __name__ == "__main__":
    unittest.main()
