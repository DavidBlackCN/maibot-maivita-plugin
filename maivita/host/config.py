"""Explicit opt-in test-instance configuration, never an R0 config upgrade."""
from collections.abc import Mapping
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from maibot_sdk import PluginConfigBase
from pydantic import Field,model_validator

VERSION='3'

ClockTime = Annotated[str, Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]


class Strict(PluginConfigBase):
    model_config={'extra':'forbid'}


class PluginSettings(Strict):
    enabled: bool=True
    config_version: Literal['3']='3'


class InstanceSettings(Strict):
    acknowledgement: Literal['','isolated-test-instance']=''
    writer_lock: str=''
    allowed_streams: list[str]=Field(default_factory=list)
    timezone: str='Asia/Shanghai'
    frequency_enabled: Literal[False]=False

    @model_validator(mode='before')
    @classmethod
    def discard_retired_permission_flags(cls, data):
        if isinstance(data, Mapping):
            # Ignore historical no-op flags without mutating the caller's input.
            return {key: value for key, value in data.items()
                    if key not in ('owner_auth_enabled', 'privileged_writes_enabled')}
        return data


def stream_allowed(stream, allowed):
    # Empty list follows all sessions admitted by Host/adapter. Never a privilege.
    return isinstance(stream, str) and bool(stream.strip()) and (not allowed or stream in allowed)


class ModelSettings(Strict):
    mode: Literal['task','model']='task'
    task: str='planner'
    model: str=''
    timeout_seconds: int=Field(default=90,ge=1,le=300)
    max_tokens: int=Field(default=8192,ge=128,le=16384)
    daily_budget: int=Field(default=20,ge=1,le=1000,description='角色时区自然日的生活模型总额度；参考计划、滚动决策、失败和重试共用。')
    generate_on_start: bool=False
    daily_planning_enabled: bool=True
    daily_planning_time: ClockTime='08:00'
    life_context: str=Field(default='',max_length=4000)


    @model_validator(mode='after')
    def validate_route(self):
        if not self.task.strip(): raise ValueError('model task must be nonempty')
        if self.mode=='model' and not self.model.strip():
            raise ValueError('explicit model required in model mode')
        return self


class WeeklyPattern(Strict):
    monday: str=''
    tuesday: str=''
    wednesday: str=''
    thursday: str=''
    friday: str=''
    saturday: str=''
    sunday: str=''


class RoutineSettings(Strict):
    initialize_body_state: bool=Field(default=True,strict=True)
    preferred_sleep_time: ClockTime='01:00'
    preferred_wake_time: ClockTime='08:00'
    weekly_pattern: WeeklyPattern=Field(default_factory=WeeklyPattern)


class SchedulerSettings(Strict):
    enabled: bool=False  # Internal legacy fixture compatibility; deployment forces rolling.
    decision_interval_minutes: int=Field(default=15,ge=1,le=1080,strict=True)
    change_chance_percent: int=Field(default=0,ge=0,le=100,strict=True)
    daily_decision_budget: int=Field(default=20,ge=1,le=1000,strict=True)
    segment_min_minutes: int=Field(default=15,ge=1,le=1080,strict=True)
    segment_max_minutes: int=Field(default=180,ge=1,le=1080,strict=True)
    segment_sleep_max_minutes: int=Field(default=720,ge=1,le=1080,strict=True)

    @model_validator(mode='after')
    def validate_limits(self):
        self.runtime_config()
        return self

    def runtime_config(self):
        from ..life.domain.segment import SchedulerConfig
        return SchedulerConfig(**self.model_dump())


class AvailabilitySettings(Strict):
    enabled: bool=Field(default=False, strict=True)
    slow_categories: list[Literal['rest','study','work','entertainment','daily_life',
                                 'social','travel','creative','other']]=Field(
        default_factory=lambda: ['study','work','travel','creative'])
    busy_when_sleeping: bool=Field(default=True, strict=True)
    busy_fatigue_threshold: int=Field(default=90,ge=0,le=100,strict=True)
    busy_energy_threshold: int=Field(default=10,ge=0,le=100,strict=True)
    followup_after_busy: Literal[False]=False

    @model_validator(mode='after')
    def validate_availability(self):
        self.runtime_config()
        return self

    def runtime_config(self):
        from ..life.planning.availability import AvailabilityConfig
        return AvailabilityConfig(**self.model_dump())


class DevelopmentConfig(Strict):
    plugin: PluginSettings=Field(default_factory=PluginSettings)
    instance: InstanceSettings=Field(default_factory=InstanceSettings)
    model: ModelSettings=Field(default_factory=ModelSettings)
    routine: RoutineSettings=Field(default_factory=RoutineSettings)
    scheduler: SchedulerSettings=Field(default_factory=SchedulerSettings)
    availability: AvailabilitySettings=Field(default_factory=AvailabilitySettings)

    @model_validator(mode='before')
    @classmethod
    def migrate_internal_config(cls,data):
        if isinstance(data,Mapping) and str(data.get('plugin',{}).get('config_version','3'))=='2':
            from copy import deepcopy
            data=deepcopy(dict(data))
            data['plugin']['config_version']='3'
        return data

    @model_validator(mode='after')
    def validate_instance(self):
        from pathlib import Path
        ZoneInfo(self.instance.timezone)
        if self.plugin.enabled:
            if self.instance.writer_lock and not Path(self.instance.writer_lock).is_absolute():
                raise ValueError('instance.writer_lock must be absolute when specified')
            if any(not s.strip() or s != s.strip() or '*' in s for s in self.instance.allowed_streams):
                raise ValueError('allowed_streams entries must be explicit stream IDs; no wildcards')
        return self
