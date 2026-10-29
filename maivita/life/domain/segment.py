"""Rolling segment configuration and model payload validation."""
from dataclasses import dataclass
from collections.abc import Mapping

from .models import number, require


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool = False
    decision_interval_minutes: int = 15
    change_chance_percent: int = 15
    daily_decision_budget: int = 20
    segment_min_minutes: int = 15
    segment_max_minutes: int = 180
    segment_sleep_max_minutes: int = 720

    def __post_init__(self):
        require(type(self.enabled) is bool, 'invalid scheduler switch')
        for name in ('decision_interval_minutes', 'daily_decision_budget',
                     'segment_min_minutes', 'segment_max_minutes', 'segment_sleep_max_minutes'):
            require(type(getattr(self, name)) is int and getattr(self, name) > 0, 'invalid '+name)
        require(type(self.change_chance_percent) is int and 0 <= self.change_chance_percent <= 100, 'invalid change chance')
        require(self.daily_decision_budget <= 1000, 'invalid decision budget')
        require(self.segment_min_minutes <= self.decision_interval_minutes <= self.segment_max_minutes,
                'decision interval must fit segment limits')
        require(self.segment_min_minutes <= self.segment_max_minutes <= self.segment_sleep_max_minutes <= 1080,
                'invalid segment limits')


def validate_segment(payload, config=None, *, current=None):
    require(isinstance(payload, Mapping), 'segment must be an object')
    required = {'event', 'location', 'emotion', 'minutes', 'continue', 'energy_rate'}
    require(required <= set(payload) <= required | {'resumes_activity_id'}, 'invalid segment fields')
    require(type(payload['continue']) is bool, 'continue must be boolean')
    for name in ('event', 'location', 'emotion'):
        require(isinstance(payload[name], str) and len(payload[name]) <= 1000, 'invalid segment '+name)
    require(payload['continue'] or payload['event'].strip(), 'segment event required')
    number(payload['energy_rate'], -0.15, 0.2)
    require(type(payload['minutes']) is int and payload['minutes'] > 0, 'invalid segment minutes')
    resume = payload.get('resumes_activity_id')
    require(resume is None or isinstance(resume, str) and bool(resume.strip()), 'invalid resume ID')
    require(not (payload['continue'] and resume), 'continue cannot resume another activity')
    if config:
        sleeping = (current.name if current and payload['continue'] else payload['event']) == '睡眠'
        maximum = config.segment_sleep_max_minutes if sleeping else config.segment_max_minutes
        require(config.segment_min_minutes <= payload['minutes'] <= maximum, 'segment duration outside bounds')
    return dict(payload)
