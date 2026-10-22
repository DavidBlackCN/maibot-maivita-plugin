import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from maibot_autonomous_planning_plugin.maivita.legacy.services.command_service import CommandService
from maibot_autonomous_planning_plugin.maivita.legacy.services.inject_service import InjectService


LOCKED_PARAMS = {
    "activity_state": "sleeping",
    "system_activity": True,
    "locked": True,
    "time_window": [60, 480],
}
FORBIDDEN = ("系统锁定", "固定睡眠块", "硬约束", "system activity", "locked")


def legacy_sleep_goal():
    return SimpleNamespace(
        goal_id="sleep-id",
        name="固定睡眠块 locked",
        description="系统锁定的固定睡眠时间（硬约束 / system activity）",
        goal_type="daily_routine",
        parameters=dict(LOCKED_PARAMS),
        conditions={},
        created_at=datetime(2026, 8, 31, 0, 0),
    )


class FakeSend:
    def __init__(self):
        self.forward_text = ""

    async def forward(self, messages, _stream_id, **kwargs):
        self.forward_text = messages[0]["segments"][0]["content"]

    async def text(self, text, _stream_id, **kwargs):
        self.forward_text = text

    async def image(self, _image, _stream_id, **kwargs):
        return None


class SleepCommandOutputTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _service_and_manager():
        manager = SimpleNamespace(
            get_schedule_goals=lambda **_kwargs: [legacy_sleep_goal()]
        )
        schedule_cfg = SimpleNamespace(
            enable_detailed_description=True,
            timezone="Asia/Shanghai",
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(schedule=schedule_cfg),
            ctx=SimpleNamespace(send=FakeSend()),
            _generation_coordinator=SimpleNamespace(
                get_status=lambda: {"state": "idle", "elapsed_seconds": 0,
                                    "current_round": 0, "current_retry": 0}
            ),
        )
        return CommandService(plugin), manager, plugin

    async def test_plan_status_hides_legacy_implementation_text(self):
        service, manager, plugin = self._service_and_manager()

        with patch(
            "maibot_autonomous_planning_plugin.maivita.legacy.services.command_service.get_goal_manager",
            return_value=manager,
        ):
            await service._handle_status("stream")

        output = plugin.ctx.send.forward_text.lower()
        self.assertIn("睡觉", output)
        self.assertNotIn("系统锁定", output)
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, output)

    async def test_plan_image_receives_only_public_sleep_text(self):
        service, manager, _plugin = self._service_and_manager()
        captured = {}

        def fake_image(**kwargs):
            captured.update(kwargs)
            return "", "encoded"

        with (
            patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.services.command_service.get_goal_manager",
                return_value=manager,
            ),
            patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.services.command_service."
                "ScheduleImageGenerator.generate_schedule_image",
                side_effect=fake_image,
            ),
        ):
            await service._handle_list("stream")

        visible = str(captured["schedule_items"]).lower()
        self.assertIn("睡觉", visible)
        self.assertNotIn("系统锁定", visible)
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, visible)

    def test_safe_generated_sleep_description_is_preserved(self):
        from maibot_autonomous_planning_plugin.maivita.legacy.core.activity_state import public_activity_text

        name, description = public_activity_text(
            "睡觉", "抱着枕头安稳地睡着，直到清晨自然醒来。", LOCKED_PARAMS
        )
        self.assertEqual(name, "睡觉")
        self.assertEqual(description, "抱着枕头安稳地睡着，直到清晨自然醒来。")


class FakeCache:
    async def get(self, _key):
        return None

    async def set(self, _key, _value):
        return None


class SleepInjectionOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_activity_and_future_activity_are_sanitized(self):
        goal = legacy_sleep_goal()
        manager = SimpleNamespace(get_active_goals=lambda **_kwargs: [goal])
        service = InjectService.__new__(InjectService)
        service._plugin = SimpleNamespace(
            config=SimpleNamespace(
                schedule=SimpleNamespace(cross_day_activity=True)
            )
        )
        service._tz_manager = SimpleNamespace(
            get_now=lambda: datetime(2026, 8, 31, 2, 0)
        )
        service._schedule_cache = FakeCache()

        with patch(
            "maibot_autonomous_planning_plugin.maivita.legacy.services.inject_service.get_goal_manager",
            return_value=manager,
        ):
            current_name, current_description, future, _goal_type = (
                await service._get_current_schedule("global")
            )

        output = f"{current_name} {current_description} {future}".lower()
        self.assertEqual(current_name, "睡觉")
        self.assertEqual(current_description, "")
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, output)


if __name__ == "__main__":
    unittest.main()
