import unittest
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("maibot_autonomous_planning_plugin", package)

try:
    from maibot_autonomous_planning_plugin.legacy_plugin import AutonomousPlanningPluginV4
except ModuleNotFoundError as exc:
    if exc.name == "maibot_sdk":
        raise unittest.SkipTest("maibot_sdk 未安装，跳过 SDK 生命周期/Hook 集成测试") from exc
    raise


class PluginRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_config_is_allowed_for_first_run(self):
        plugin = AutonomousPlanningPluginV4()
        normalized, _changed = plugin.normalize_plugin_config({})
        self.assertIsInstance(normalized, dict)

    async def test_legacy_config_is_rejected_instead_of_migrated(self):
        plugin = AutonomousPlanningPluginV4()
        legacy = {
            "plugin": {"config_version": "5.0.0"},
            "schedule": {"llm_task_name": "planner"},
        }
        with self.assertRaisesRegex(ValueError, "不支持直接迁移.*llm_task_name"):
            plugin.normalize_plugin_config(legacy)

    async def test_nonempty_config_without_version_is_rejected(self):
        plugin = AutonomousPlanningPluginV4()
        with self.assertRaisesRegex(ValueError, "config_version=缺失"):
            plugin.normalize_plugin_config({"schedule": {"auto_generate": True}})

    async def test_every_removed_config_field_is_rejected(self):
        plugin = AutonomousPlanningPluginV4()
        cases = [
            {"schedule": {"sync_sleep_frequency": True}},
            {"schedule": {"sleep_talk_adjust": 0.0}},
            {"schedule": {"enable_frequency_modulation": True}},
            {"schedule": {"day_start_time": "23:00"}},
            {"schedule": {"day_end_time": "07:00"}},
            {"schedule": {"llm_task_name": "replyer"}},
            {"inject": {"inject_mode": "smart"}},
            {"autonomous_planning": {"schedule": {}}},
        ]
        for sections in cases:
            config = {"plugin": {"config_version": "5.1.0"}, **sections}
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "已删除字段"):
                plugin.normalize_plugin_config(config)

    async def test_bot_profile_hot_reload(self):
        plugin = AutonomousPlanningPluginV4.__new__(AutonomousPlanningPluginV4)
        plugin._bot_profile = {"bot_name": "旧名字"}

        async def prefetch():
            return {"bot_name": "新名字", "personality": "新人设"}

        plugin._prefetch_bot_profile = prefetch
        await AutonomousPlanningPluginV4.on_config_update(plugin, "bot", {}, "2")
        self.assertEqual(plugin._bot_profile["bot_name"], "新名字")

    async def test_plugin_config_hot_reload_refreshes_frequency_immediately(self):
        plugin = AutonomousPlanningPluginV4.__new__(AutonomousPlanningPluginV4)

        class StubProactiveService:
            refreshed = 0

            async def refresh_frequency(self):
                self.refreshed += 1

        plugin._proactive_svc = StubProactiveService()
        await AutonomousPlanningPluginV4.on_config_update(plugin, "self", {}, "3")
        self.assertEqual(plugin._proactive_svc.refreshed, 1)

    async def test_sleep_send_hook_aborts_without_dropping_input(self):
        plugin = AutonomousPlanningPluginV4.__new__(AutonomousPlanningPluginV4)
        plugin.is_sleeping_now = lambda: True
        result = await AutonomousPlanningPluginV4.handle_sleep_silence(plugin)
        self.assertEqual(result["action"], "abort")

    async def test_sleep_send_hook_allows_scoped_command_reply_only(self):
        plugin = AutonomousPlanningPluginV4.__new__(AutonomousPlanningPluginV4)
        plugin.is_sleeping_now = lambda: True
        from maibot_autonomous_planning_plugin.integrations.safety import ReplyPermits
        plugin._reply_permits = ReplyPermits()
        observed = []

        async def send_command_response(token):
            observed.append(
                await plugin.handle_sleep_silence(message={"session_id": "stream-1", "maivita_receipt_token": token,
                                                           "source_plugin_id": "davidblackcn.maibot-maivita-plugin"})
            )
            observed.append(
                await plugin.handle_sleep_silence(message={"session_id": "stream-2"})
            )
            return "sent"

        result = await plugin.send_command_reply("stream-1", send_command_response)

        self.assertEqual(result, "sent")
        self.assertEqual(observed[0]["action"], "continue")
        self.assertEqual(observed[1]["action"], "abort")
        self.assertEqual(plugin._reply_permits.pending, {})
        after = await plugin.handle_sleep_silence(message={"session_id": "stream-1"})
        self.assertEqual(after["action"], "abort")

    async def test_planning_command_stays_intercepted(self):
        plugin = AutonomousPlanningPluginV4.__new__(AutonomousPlanningPluginV4)

        class StubCommandService:
            async def execute(self, **kwargs):
                self.kwargs = kwargs
                return True, "命令执行完成", True

        plugin._cmd_svc = StubCommandService()
        result = await plugin.handle_planning_command(
            text="/plan status", stream_id="stream-1", user_id="user-1"
        )

        self.assertEqual(result, (True, "命令执行完成", True))


if __name__ == "__main__":
    unittest.main()
