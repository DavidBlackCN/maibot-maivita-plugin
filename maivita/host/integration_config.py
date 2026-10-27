"""Version 3 deployment configuration; one explicitly selected engine."""
from typing import Literal
from pydantic import Field, model_validator
from .config import DevelopmentConfig, PluginSettings, Strict
from ..legacy.config_models import MaiVitaConfig as LegacyConfig


class PluginConfig(PluginSettings):
    config_version: Literal['3'] = '3'


class EngineConfig(Strict):
    mode: Literal['life', 'legacy'] = 'life'


class IntegrationConfig(DevelopmentConfig):
    plugin: PluginConfig = Field(default_factory=PluginConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    legacy: LegacyConfig = Field(default_factory=lambda: LegacyConfig(plugin={'enabled': False}))

    @model_validator(mode='after')
    def validate_engine(self):
        if self.plugin.enabled and self.engine.mode == 'legacy':
            if self.legacy.compatibility.legacy_empty_admin_all:
                raise ValueError('resolve legacy open-admin semantics before activation')
            if not self.legacy.admin.legacy_admin_users:
                raise ValueError('legacy engine needs explicit administrator IDs')
        return self

    def resolved(self, data):
        from pathlib import Path
        from .activation import read_record
        result = self.model_copy(deep=True)
        if not result.instance.writer_lock:
            # Preserve an existing deployment's authoritative lock on upgrade.
            record = read_record(Path(data) / 'engine-authority.json')
            result.instance.writer_lock = (record['writer_lock'] if record else str(Path(data) / 'writer.lock'))
        return type(self).model_validate(result.model_dump())

    def selected_config(self):
        if self.engine.mode == 'life':
            return DevelopmentConfig.model_validate({
                'plugin': {'enabled': self.plugin.enabled, 'config_version': '2'},
                'instance': self.instance.model_dump(), 'model': self.model.model_dump()})
        legacy = self.legacy.model_copy(deep=True)
        legacy.plugin.enabled = self.plugin.enabled
        legacy.storage.writer_lock = self.instance.writer_lock
        legacy.storage.data_dir = ''  # Only the granted, engine-scoped SDK path.
        legacy.integration.allowed_streams = list(self.instance.allowed_streams)
        legacy.character.timezone = self.instance.timezone
        legacy.compatibility.frequency.enable_sleep = False
        legacy.compatibility.frequency.enable_activity = False
        return LegacyConfig.model_validate(legacy.model_dump())
