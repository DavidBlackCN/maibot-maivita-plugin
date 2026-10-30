"""Deployment paths for the current configuration."""
from .config import DevelopmentConfig


class IntegrationConfig(DevelopmentConfig):
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
        return DevelopmentConfig.model_validate(self.model_dump())
