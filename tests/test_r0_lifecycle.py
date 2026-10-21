"""Lifecycle regression with a real SDK context and disposable data only."""
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from r0_support import ROOT
from maibot_sdk.context import PluginContext, PluginPaths
from maibot_autonomous_planning_plugin.legacy_plugin import MaiVitaPlugin
from maibot_autonomous_planning_plugin.config_models import MaiVitaConfig
from maibot_autonomous_planning_plugin.planner.goal_manager import close_goal_manager, get_goal_manager
from maibot_autonomous_planning_plugin.storage.safety import WriterLease
from maibot_autonomous_planning_plugin.integrations.safety import OLD_ID, PLUGIN_ID


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.plugins = []
        self.loaded = []
        self.calls = []

    async def asyncTearDown(self):
        for plugin in self.plugins:
            await plugin.on_unload()
        close_goal_manager()
        self.temp.cleanup()

    def make_plugin(self, dirname="new"):
        async def rpc(method, plugin_id, payload):
            self.calls.append((plugin_id, payload))
            if payload["capability"] == "component.list_loaded_plugins":
                return {"success": True, "plugins": self.loaded}
            if payload["capability"] == "config.get":
                return {"success": True, "value": "synthetic profile"}
            raise AssertionError("unexpected external call: " + payload["capability"])
        ctx = PluginContext(PLUGIN_ID, rpc_call=rpc, paths=PluginPaths(self.root / dirname, self.root / "runtime"))
        plugin = MaiVitaPlugin()
        plugin._set_context(ctx)
        config = MaiVitaConfig().model_dump()
        config["storage"]["writer_lock"] = str(self.root / "shared-writer.lock")
        config["planning"]["daily_enabled"] = False
        config["observability"]["model_logs_enabled"] = False
        plugin.set_plugin_config(config)
        self.plugins.append(plugin)
        return plugin

    async def test_T05_load_unload_reload_tasks_manager_and_path_are_owned(self):
        plugin = self.make_plugin()
        await plugin.on_load()
        self.assertTrue(plugin._loaded)
        self.assertEqual(len(plugin._bg_tasks), 4)
        self.assertEqual(get_goal_manager().data_dir, self.root / "new")
        self.assertEqual(plugin.llm_log_dir, self.root / "new" / "llm_logs")
        old_tasks = list(plugin._bg_tasks)
        await plugin.on_unload()
        self.assertTrue(all(task.done() for task in old_tasks))
        self.assertEqual(plugin._bg_tasks, [])
        self.assertEqual((await plugin.api_get_current_activity())["error"], "plugin_not_initialized")
        self.assertEqual((await plugin.handle_sleep_silence(message={"session_id": "g"}))["action"], "continue")
        await plugin.on_load()
        self.assertTrue(plugin._loaded)
        self.assertFalse(any(task in old_tasks for task in plugin._bg_tasks))
        await plugin.on_config_update("self", {}, "2")
        self.assertTrue(plugin._loaded)
        self.assertEqual(len(plugin._bg_tasks), 4)

    async def test_T05_loaded_legacy_writer_and_second_new_writer_are_blocked(self):
        plugin = self.make_plugin()
        self.loaded = [OLD_ID]
        with self.assertRaisesRegex(RuntimeError, "old full plugin"):
            await plugin.on_load()
        self.assertFalse((self.root / "new").exists())
        self.loaded = []
        await plugin.on_load()
        second = self.make_plugin("second")
        with self.assertRaisesRegex(RuntimeError, "another life writer"):
            await second.on_load()
        self.assertFalse((self.root / "second").exists())
        manager = get_goal_manager()
        await second.on_unload()  # failed contender must not close the owner's DB
        self.assertIs(get_goal_manager(), manager)
        self.assertEqual(manager.db.get_all_goals(), [])
        await plugin.on_unload()
        await second.on_load()
        self.assertTrue(second._loaded)

    async def test_T05_unknown_registry_and_missing_lock_fail_before_database(self):
        plugin = self.make_plugin()
        self.loaded = [{"unexpected": "shape"}]
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            await plugin.on_load()
        self.loaded = []
        config = plugin.config.model_dump()
        config["storage"]["writer_lock"] = ""
        plugin.set_plugin_config(config)
        with self.assertRaisesRegex(ValueError, "writer_lock"):
            await plugin.on_load()
        self.assertFalse((self.root / "new").exists())

    async def test_T05_partial_start_failure_releases_lease_without_tasks(self):
        plugin = self.make_plugin()
        with patch("maibot_autonomous_planning_plugin.legacy_plugin.ToolsService", side_effect=RuntimeError("startup failure")):
            with self.assertRaisesRegex(RuntimeError, "startup failure"):
                await plugin.on_load()
        self.assertEqual(plugin._bg_tasks, [])
        self.assertIsNone(plugin._writer_lease)
        lease = WriterLease(self.root / "shared-writer.lock")
        lease.acquire()
        lease.release()
        await plugin.on_load()
        self.assertTrue(plugin._loaded)

    async def test_T05_sleeping_hot_disable_stops_writer_and_send_guard(self):
        plugin = self.make_plugin()
        await plugin.on_load()
        config = plugin.config.model_dump()
        config["plugin"]["enabled"] = False
        plugin.set_plugin_config(config)
        await plugin.on_config_update("self", {}, "2")
        self.assertFalse(plugin._loaded)
        self.assertEqual(plugin._bg_tasks, [])
        self.assertEqual((await plugin.handle_sleep_silence(message={"session_id": "g"}))["action"], "continue")
        config["plugin"]["enabled"] = True
        plugin.set_plugin_config(config)
        await plugin.on_config_update("self", {}, "3")
        self.assertTrue(plugin._loaded)

    async def test_legacy_fixed_sleep_boundaries_and_explicit_disabled_with_controlled_clock(self):
        plugin = self.make_plugin()
        config = plugin.config.model_dump()
        config["compatibility"].update(legacy_sleep_start_time="23:30", legacy_sleep_end_time="08:00")
        plugin.set_plugin_config(config)
        for hour, minute, expected in [(23, 29, False), (23, 30, True), (0, 0, True), (7, 59, True), (8, 0, False)]:
            plugin._inject_svc = NS(_tz_manager=NS(get_now=lambda: datetime(2026, 10, 21, hour, minute)))
            self.assertEqual(plugin.is_sleeping_now(), expected)
        config["sleep"]["mode"] = "disabled"
        plugin.set_plugin_config(config)
        self.assertFalse(plugin.is_sleeping_now())


if __name__ == "__main__":
    unittest.main()
