"""Version 3 deployment configuration for the life engine."""
import logging
from collections.abc import Mapping
from threading import Lock
from typing import Literal
from pydantic import Field, model_validator
from .config import DevelopmentConfig, PluginSettings


logger = logging.getLogger(__name__)
_retired_warning_lock = Lock()
_retired_warning_logged = False


def _warn_retired_sections(sections):
    """Warn once per process, including across validation and config reloads."""
    global _retired_warning_logged
    with _retired_warning_lock:
        if not _retired_warning_logged:
            logger.warning('检测到已退役的 %s 配置段，已忽略。请参照 config.example.toml 清理。',
                           '、'.join(f'[{name}]' for name in sections))
            _retired_warning_logged = True


class PluginConfig(PluginSettings):
    config_version: Literal['3'] = '3'


class IntegrationConfig(DevelopmentConfig):
    model_config = {'extra': 'forbid'}
    plugin: PluginConfig = Field(default_factory=PluginConfig)

    @model_validator(mode='before')
    @classmethod
    def discard_retired_sections(cls, data):
        if isinstance(data, Mapping):
            retired = tuple(name for name in ('engine', 'legacy') if name in data)
            if retired:
                _warn_retired_sections(retired)
                # Do not retain retired values or mutate the caller's config.
                return {key: value for key, value in data.items() if key not in retired}
        return data

    def resolved(self, data):
        from pathlib import Path
        from .activation import read_record
        result = self.model_copy(deep=True)
        if not result.instance.writer_lock:
            # Preserve an existing deployment's authoritative lock on upgrade.
            record = read_record(Path(data) / 'engine-authority.json')
            result.instance.writer_lock = (record['writer_lock'] if record else str(Path(data) / 'writer.lock'))
        return type(self).model_validate(result.model_dump())

    def selected_config(self) -> DevelopmentConfig:
        return DevelopmentConfig.model_validate({
            'plugin': {'enabled': self.plugin.enabled, 'config_version': '2'},
            'instance': self.instance.model_dump(), 'model': self.model.model_dump(),
            'routine': self.routine.model_dump()})
