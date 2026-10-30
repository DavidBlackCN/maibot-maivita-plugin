"""Recognize short direct invitations, then verify Host admission before offering."""
import asyncio
import math
import re
from collections import OrderedDict

from .config import stream_allowed
from ..life.planning.conversation import offer

INVITATION = re.compile(r'(?:你)?(?:现在)?(?:能不能|可以|能|要不要)?(?:先)?'
    r'(?:(?:陪我|和我|跟我)聊(?:一?会儿?|聊(?:天)?|天)|聊会儿天)'
    r'(?:吗|么|吧|好吗|好不好)?[？?！!。~～]*')


def invitation(message, stream, now):
    if not isinstance(message, dict) or message.get('session_id') != stream:
        return False
    info = message.get('message_info') or {}
    if not isinstance(info, dict):
        return False
    user = info.get('user_info') or {}
    if not isinstance(user, dict):
        return False
    account = message.get('account_id')
    if (info.get('group_info') or not user.get('user_id') or not account
            or str(user['user_id']) == str(account) or message.get('is_notify') or message.get('is_command')):
        return False
    try:
        age = now - float(message['timestamp'])
    except (KeyError, TypeError, ValueError):
        return False
    if not math.isfinite(age) or not 0 <= age < 300:
        return False
    raw = message.get('raw_message')
    if not isinstance(raw, list) or not raw or any(not isinstance(c, dict) or c.get('type') != 'text' for c in raw):
        return False
    text = ''.join(c.get('data', '') for c in raw if isinstance(c.get('data'), str)).strip()
    return len(text) <= 80 and INVITATION.fullmatch(text) is not None


class ConversationObserver:
    def __init__(self, plugin):
        self.plugin = plugin
        self.pending = OrderedDict()

    def observe(self, message):
        p = self.plugin
        stream = message.get('session_id') if isinstance(message, dict) else None
        if not p.ready or not stream_allowed(stream, p.active_config.instance.allowed_streams):
            return
        now = p.runtime.engine.clock.now_utc().timestamp()
        if p.runtime.engine.state().active_sleep_session_id or not invitation(message, stream, now):
            return
        key = message.get('message_id')
        if not isinstance(key, str) or not key or len(key) > 256:
            return
        self.pending[stream] = (key, now)
        self.pending.move_to_end(stream)
        while len(self.pending) > 64:
            self.pending.popitem(last=False)

    async def confirm(self, stream):
        p = self.plugin
        pending = self.pending.pop(stream, None)
        if not pending or not p.ready or not stream_allowed(stream, p.active_config.instance.allowed_streams):
            return
        runtime = p.runtime
        now = runtime.engine.clock.now_utc().timestamp()
        if not 0 <= now - pending[1] < 300 or runtime.engine.state().active_sleep_session_id:
            return
        try:
            message = await asyncio.wait_for(p.ctx.message.get_by_id(pending[0], chat_id=stream), .5)
        except Exception:
            # Lookup failure is a missed optional opportunity, never a retry queue.
            return
        if p.ready and p.runtime is runtime and invitation(message, stream, now) and message.get('message_id') == pending[0]:
            offer(runtime.engine)
