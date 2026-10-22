"""Explicit opt-in test-instance configuration, never an R0 config upgrade."""
from typing import Literal
from zoneinfo import ZoneInfo

from maibot_sdk import PluginConfigBase
from pydantic import Field,model_validator

VERSION='2'


class Strict(PluginConfigBase):
    model_config={'extra':'forbid'}


class PluginSettings(Strict):
    enabled: bool=False
    config_version: Literal['2']='2'


class InstanceSettings(Strict):
    acknowledgement: Literal['','isolated-test-instance']=''
    writer_lock: str=''
    allowed_streams: list[str]=Field(default_factory=list)
    timezone: str='Asia/Shanghai'
    frequency_enabled: Literal[False]=False
    owner_auth_enabled: Literal[False]=False
    privileged_writes_enabled: Literal[False]=False


class ModelSettings(Strict):
    task_name: str='utils'
    model_name: str=''
    timeout_seconds: int=Field(default=25,ge=1,le=25)
    max_tokens: int=Field(default=2048,ge=128,le=8192)
    daily_budget: int=Field(default=8,ge=1,le=100)
    generate_on_start: bool=False
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
            if self.instance.acknowledgement!='isolated-test-instance':
                raise ValueError('I1 requires an explicit isolated-test-instance acknowledgement')
            if not self.instance.writer_lock or not Path(self.instance.writer_lock).is_absolute():
                raise ValueError('I1 requires an absolute instance-wide writer_lock')
            if not self.instance.allowed_streams or any(not s.strip() or '*' in s for s in self.instance.allowed_streams):
                raise ValueError('I1 requires explicit test stream IDs; no wildcards')
        if not self.model.task_name.strip(): raise ValueError('explicit model task_name required')
        return self
