"""Explicit opt-in test-instance configuration, never an R0 config upgrade."""
from collections.abc import Mapping
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from maibot_sdk import PluginConfigBase
from pydantic import Field,model_validator

VERSION='2'

ClockTime = Annotated[str, Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]


class Strict(PluginConfigBase):
    model_config={'extra':'forbid'}


class PluginSettings(Strict):
    enabled: bool=True
    config_version: Literal['2']='2'


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
    daily_budget: int=Field(default=16,ge=1,le=200)
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
    preferred_sleep_time: ClockTime='01:00'
    preferred_wake_time: ClockTime='08:00'
    weekly_pattern: WeeklyPattern=Field(default_factory=WeeklyPattern)


class DevelopmentConfig(Strict):
    plugin: PluginSettings=Field(default_factory=PluginSettings)
    instance: InstanceSettings=Field(default_factory=InstanceSettings)
    model: ModelSettings=Field(default_factory=ModelSettings)
    routine: RoutineSettings=Field(default_factory=RoutineSettings)

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
