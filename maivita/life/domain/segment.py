"""Rolling segment configuration and model payload validation."""
from dataclasses import dataclass
from collections.abc import Mapping

from .models import number, require


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool = False
    decision_interval_minutes: int = 15
    segment_min_minutes: int = 15
    segment_max_minutes: int = 180
    segment_sleep_max_minutes: int = 720

    def __post_init__(self):
        require(type(self.enabled) is bool, 'invalid scheduler switch')
        for name in ('decision_interval_minutes',
                     'segment_min_minutes', 'segment_max_minutes', 'segment_sleep_max_minutes'):
            require(type(getattr(self, name)) is int and getattr(self, name) > 0, 'invalid '+name)
        require(self.segment_min_minutes <= self.decision_interval_minutes <= self.segment_max_minutes,
                'decision interval must fit segment limits')
        require(self.segment_min_minutes <= self.segment_max_minutes <= self.segment_sleep_max_minutes <= 1080,
                'invalid segment limits')


def validate_segment(payload, config=None, *, current=None):
    require(isinstance(payload, Mapping), 'segment must be an object')
    required = {'event', 'location', 'emotion', 'minutes', 'continue'}
    require(required <= set(payload) <= required | {'resumes_activity_id', 'category', 'energy_rate', 'sleep', 'pause_for_chat'}, 'invalid segment fields')
    # Old saved candidates remain readable. New candidates use categories only.
    category = payload.get('category', 'rest' if payload.get('energy_rate', 0) > 0 else 'other')
    require(category in ('rest','study','work','entertainment','daily_life','social','travel','creative','other'), 'invalid activity category')
    require(type(payload['continue']) is bool, 'continue must be boolean')
    if 'sleep' in payload:
        require(type(payload['sleep']) is bool, 'sleep must be boolean')
    for name in ('event', 'location', 'emotion'):
        require(isinstance(payload[name], str) and len(payload[name]) <= 1000, 'invalid segment '+name)
    require(payload['continue'] or payload['event'].strip(), 'segment event required')
    if 'energy_rate' in payload: number(payload['energy_rate'], -0.15, 0.2)
    require(type(payload['minutes']) is int and payload['minutes'] > 0, 'invalid segment minutes')
    pause = payload.get('pause_for_chat')
    require(pause is None or isinstance(pause, str) and bool(pause), 'invalid invitation ID')
    require(not pause or not payload['continue'] and not payload.get('resumes_activity_id') and not payload.get('sleep', False), 'chat pause must be a new awake activity')
    resume = payload.get('resumes_activity_id')
    require(resume is None or isinstance(resume, str) and bool(resume.strip()), 'invalid resume ID')
    require(not (payload['continue'] and resume), 'continue cannot resume another activity')
    # Explicit intent wins over wording. Only old candidates without the field
    # use the historical exact name; existing sleep is identified by its session.
    sleeping = payload.get('sleep', payload['event'].strip() == '睡眠')
    if current is not None and payload['continue']:
        sleeping = current.sleep_session_id is not None
        require('sleep' not in payload or payload['sleep'] == sleeping,
                'continue cannot change sleep state')
    require(not (resume and sleeping), 'sleep cannot resume an awake activity')
    require(not (pause and sleeping), 'chat pause cannot start sleep')
    if config:
        maximum = config.segment_sleep_max_minutes if sleeping else config.segment_max_minutes
        require(config.segment_min_minutes <= payload['minutes'] <= maximum, 'segment duration outside bounds')
    result = {**dict(payload), 'category': category}
    if not payload['continue'] or current is not None or 'sleep' in payload:
        result['sleep'] = sleeping
    return result
