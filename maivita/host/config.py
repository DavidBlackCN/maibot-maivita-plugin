"""Explicit opt-in test-instance configuration, never an R0 config upgrade."""
from typing import Literal
from zoneinfo import ZoneInfo

from maibot_sdk import PluginConfigBase
from pydantic import Field,model_validator

VERSION='2'


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
    owner_auth_enabled: Literal[False]=False
    privileged_writes_enabled: Literal[False]=False


def stream_allowed(stream, allowed):
    # Empty list follows all sessions admitted by Host/adapter. Never a privilege.
    return isinstance(stream, str) and bool(stream.strip()) and (not allowed or stream in allowed)


class ModelSettings(Strict):
    task_name: str='utils'
    model_name: str=''
    timeout_seconds: int=Field(default=25,ge=1,le=25)
    max_tokens: int=Field(default=2048,ge=128,le=8192)
    daily_budget: int=Field(default=8,ge=1,le=100)
    generate_on_start: bool=False
    daily_planning_enabled: bool=False
    life_context: str=Field(default='',max_length=4000)


class DevelopmentConfig(Strict):
    plugin: PluginSettings=Field(default_factory=PluginSettings)
    instance: InstanceSettings=Field(default_factory=InstanceSettings)
    model: ModelSettings=Field(default_factory=ModelSettings)

    @model_validator(mode='after')
    def validate_instance(self):
        from pathlib import Path
        ZoneInfo(self.instance.timezone)
        if self.plugin.enabled:
            if self.instance.writer_lock and not Path(self.instance.writer_lock).is_absolute():
                raise ValueError('instance.writer_lock must be absolute when specified')
            if any(not s.strip() or s != s.strip() or '*' in s for s in self.instance.allowed_streams):
                raise ValueError('allowed_streams entries must be explicit stream IDs; no wildcards')
        if not self.model.task_name.strip(): raise ValueError('explicit model task_name required')
        return self
