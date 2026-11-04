"""Ordinary group participation, not a send permit or private-chat probability."""
from datetime import timedelta
from ..domain.clock import iso_instant
from .availability import derive_availability


def derive(state,activity,config,now,previous=1.0):
    availability=derive_availability(state,activity,config,activity_visible=bool(activity and activity.visibility=='public_summary'))
    blocked=state.active_sleep_session_id is not None or config.enabled and availability.state=='busy'
    if blocked:
        band,multiplier,reason='paused',1.0,'睡眠中' if state.active_sleep_session_id else '需要休息'
    elif availability.state=='slow':
        band,multiplier,reason='focused',0.3,availability.reason
    else:
        tired=(state.fatigue is not None and state.fatigue >= (50 if previous==0.6 else 60)) or (
            state.energy is not None and state.energy <= (50 if previous==0.6 else 40)) or (
            state.sleepiness is not None and state.sleepiness >= (55 if previous==0.6 else 65))
        band,multiplier,reason=('quiet',0.6,'有些疲倦或困倦') if tired else ('normal',1.0,'可以正常交流')
    return {'version':1,'band':band,'multiplier':multiplier,'reason':reason,
            'proactive_allowed':not blocked and multiplier>0.3,
            'cooldown_factor':2 if multiplier==0.6 else 1,
            'as_of':iso_instant(now),'valid_until':iso_instant(now+timedelta(seconds=30)),
            'send_permit':False}
