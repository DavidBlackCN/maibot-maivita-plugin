"""No stream-wide send permits and no global frequency resets."""
from __future__ import annotations
import inspect
import logging
import secrets
import time

logger = logging.getLogger(__name__)
PLUGIN_ID = "davidblackcn.maibot-maivita-plugin"
OLD_ID = "davidblackcn.maibot-autonomous-planning-plugin"


class ReplyPermits:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending = {}

    def issue(self, stream):
        token = secrets.token_urlsafe(32)
        self.pending[token] = (stream, self.clock() + 10)
        return token

    def consume(self, message):
        if not isinstance(message, dict):
            return False
        token = message.get("maivita_receipt_token")
        if not isinstance(token, str):
            return False
        permit = self.pending.get(token)
        if not permit or message.get("source_plugin_id") != PLUGIN_ID:
            return False
        stream, expires = permit
        if message.get("session_id") != stream or self.clock() > expires:
            return False
        self.pending.pop(token, None)
        return True


class OwnedFrequency:
    """Use only a verified owner-aware API. SDK 2.15's global API is refused.

    Duck-typing here describes the adapter port, not a claim that deployed SDKs
    provide it. Real SDK tests verify the refusal as well as RPC call identity.
    """
    adjustment_id = PLUGIN_ID + ":legacy_life"

    def __init__(self, capability):
        self.capability = capability
        self.available = (
            callable(getattr(capability, "remove_adjust", None)) and
            "adjustment_id" in inspect.signature(capability.set_adjust).parameters and
            "adjustment_id" in inspect.signature(capability.remove_adjust).parameters
        )
        self.warned = False

    async def set_adjust(self, stream, value):
        if not self.available:
            if not self.warned:
                logger.error("frequency_owned_api_unavailable: global adjustment left unchanged")
                self.warned = True
            return False
        return await self.capability.set_adjust(stream, value, adjustment_id=self.adjustment_id)

    async def remove_adjust(self, stream):
        if not self.available:
            return False
        return await self.capability.remove_adjust(stream, adjustment_id=self.adjustment_id)


async def assert_no_old_writer(ctx):
    """Inspect the loaded registry; an API-only bridge is allowed separately."""
    loaded = await ctx.component.list_loaded_plugins()
    if not isinstance(loaded, list):
        raise RuntimeError("unverified loaded-plugin registry shape; refusing writer startup")
    for entry in loaded:
        if isinstance(entry, str):
            if entry == OLD_ID:
                raise RuntimeError("old full plugin is active; stop old writer")
        elif isinstance(entry, dict):
            identity = entry.get("plugin_id") or entry.get("id") or entry.get("name")
            if identity == OLD_ID:
                raise RuntimeError("old full plugin is active")
            if not identity:
                raise RuntimeError("unknown loaded-plugin registry identity")
        else:
            raise RuntimeError("unknown loaded-plugin registry entry")
