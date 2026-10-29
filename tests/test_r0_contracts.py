"""T04/T05: retained SDK aliases, frequency refusal and receipt token contracts."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from r0_support import host_classes
from maibot_sdk.context import PluginContext
from maibot_autonomous_planning_plugin.maivita.host.plugin import MaiVitaDevelopmentPlugin as MaiVitaPlugin
from maibot_autonomous_planning_plugin.maivita.integrations.safety import OwnedFrequency, ReplyPermits, PLUGIN_ID, OLD_ID


class SDKContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_T04_real_SDK_full_legacy_route_through_real_host_registry_and_resolver(self):
        registry_module = host_classes("src/plugin_runtime/host/api_registry.py", {"APIEntry", "APIRegistry"})
        component_module = host_classes("src/plugin_runtime/capabilities/components.py", {"RuntimeComponentCapabilityMixin"})
        plugin = MaiVitaPlugin()
        registry = registry_module.APIRegistry()
        count = registry.register_plugin_apis(PLUGIN_ID, [{**c, "component_type": c["type"]} for c in plugin.get_components()])
        self.assertEqual(count, len([c for c in plugin.get_components() if c['type'] == 'API']))
        async def invoke_api(plugin_id, component_name, args, timeout_ms):
            self.assertEqual(plugin_id, PLUGIN_ID)
            value = await getattr(plugin, component_name)(**args)
            return NS(error=None, payload={"success": True, "result": value})
        supervisor = NS(api_registry=registry, invoke_api=invoke_api)
        manager = component_module.RuntimeComponentCapabilityMixin()
        manager.supervisors = [supervisor]
        calls = []
        async def rpc(method, plugin_id, payload, **kwargs):
            calls.append((method, plugin_id, payload))
            return await manager._cap_api_call(plugin_id, "api.call", payload["args"])
        consumer = PluginContext("synthetic.consumer", rpc_call=rpc)
        old = await consumer.api.call(OLD_ID + ".get_current_activity", chat_id="global", ignored=True)
        new = await consumer.api.call(PLUGIN_ID + ".get_current_activity", chat_id="global")
        self.assertEqual(old, new)
        self.assertEqual(old["error"], "NOT_READY")
        self.assertEqual(set(old), {"has_activity", "activity", "error"})
        # A live service projection follows both routes without another writer.
        plugin.get_current_activity = AsyncMock(return_value={**old, "error": None, "as_of": "fixture"})
        active_old = await consumer.api.call(OLD_ID + ".get_current_activity", chat_id="global")
        active_new = await consumer.api.call(PLUGIN_ID + ".get_current_activity", chat_id="global")
        self.assertEqual(active_old, active_new)
        self.assertEqual(active_old["as_of"], "fixture")
        self.assertTrue(calls)

    async def test_T05_real_SDK_global_frequency_is_refused_without_any_RPC_write(self):
        rpc = AsyncMock()
        ctx = PluginContext(PLUGIN_ID, rpc_call=rpc)
        adapter = OwnedFrequency(ctx.frequency)
        self.assertFalse(adapter.available)
        self.assertFalse(await adapter.set_adjust("group", 0))
        self.assertFalse(await adapter.remove_adjust("group"))
        rpc.assert_not_awaited()

    async def test_T05_owned_frequency_port_removes_only_this_adjustment(self):
        values = {("g", "other.plugin"): 0.7}
        class Capability:
            async def set_adjust(self, stream, value, *, adjustment_id):
                values[stream, adjustment_id] = value
                return True
            async def remove_adjust(self, stream, *, adjustment_id):
                values.pop((stream, adjustment_id), None)
                return True
        owned = OwnedFrequency(Capability())
        self.assertTrue(owned.available)
        await owned.set_adjust("g", 0)
        await owned.remove_adjust("g")
        self.assertEqual(values, {("g", "other.plugin"): 0.7})

    async def test_T05_receipt_token_exact_stream_once_and_expiry(self):
        now = [0]
        permits = ReplyPermits(clock=lambda: now[0])
        token = permits.issue('same-stream')
        message = {"session_id": "other", "maivita_receipt_token": token, "source_plugin_id": PLUGIN_ID}
        self.assertFalse(permits.consume(message))
        message["session_id"] = "same-stream"
        message["source_plugin_id"] = "forged-other-source"
        self.assertFalse(permits.consume(message))
        message["source_plugin_id"] = PLUGIN_ID
        self.assertTrue(permits.consume(message))
        self.assertFalse(permits.consume(message))
        expired = permits.issue("same-stream")
        now[0] = 11
        self.assertFalse(permits.consume({**message, "maivita_receipt_token": expired}))

    async def test_T05_real_SDK_forwards_receipt_kwarg(self):
        rpc = AsyncMock(return_value={"success": True})
        ctx = PluginContext(PLUGIN_ID, rpc_call=rpc)
        await ctx.send.text("receipt", "g", maivita_receipt_token="fixture-token")
        self.assertIn("maivita_receipt_token", rpc.call_args.args[2]["args"])


if __name__ == "__main__":
    unittest.main()
