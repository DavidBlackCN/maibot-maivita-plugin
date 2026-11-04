"""Private Host policy; no model call, timer, or permission granted by kwargs."""
from .config import stream_allowed
from ..life.planning.availability import derive_availability


def control(plugin):
    reader = getattr(plugin._ctx, 'behavior_context', None)
    return reader() if reader is not None else {}


def evaluate(plugin):
    request = control(plugin).get('request')
    result = {'version': 1, 'decision': 'deny', 'reason': 'untrusted_request'}
    if not isinstance(request, dict):
        return result
    if not plugin.ready or plugin.active_config is None or plugin.runtime is None:
        return {**result, 'reason': 'not_ready'}
    stream = request.get('stream_id')
    if not isinstance(stream, str) or not stream:
        return {**result, 'reason': 'unknown_route'}
    if not stream_allowed(stream, plugin.active_config.instance.allowed_streams):
        return {**result, 'decision': 'abstain', 'reason': 'not_managed'}
    state = plugin.runtime.engine.state()
    if state.sleep_phase in ('sleeping', 'temporarily_awake'):
        return {**result, 'reason': 'sleeping'}
    config = plugin.active_config.availability
    if request.get('category') != 'cognition' and config.enabled and derive_availability(state, None, config).state == 'busy':
        return {**result, 'reason': 'busy'}
    return {**result, 'decision': 'allow', 'reason': 'available', 'revision': state.state_version}
