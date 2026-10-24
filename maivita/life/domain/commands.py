"""Internal command envelope. Integration boundaries must authenticate actors."""
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from typing import Any

from .clock import as_utc
from .models import freeze_json, integer, require, text


@dataclass(frozen=True, slots=True, kw_only=True)
class Command:
    idempotency_key: str
    kind: str
    expected_state_version: int
    payload: Mapping[str, Any]
    character_id: str = "default"
    source: str = "internal"
    decision_id: str | None = None
    expected_plan_version: int | None = None
    expected_config_revision: int | None = None
    expires_at: datetime | None = None

    def __post_init__(self):
        for value in (self.idempotency_key, self.kind, self.character_id):
            text(value)
        integer(self.expected_state_version)
        require(self.source in ('internal', 'legacy_observed', 'model', 'authorized'), "unknown command source")
        require(isinstance(self.payload, Mapping), "command payload must be an object")
        # These records have no authenticated source field in schema 4. Keep
        # old rows readable, but reject new promises of source-only visibility.
        records = [self.payload] if self.kind in ('start_activity', 'start_secondary', 'manage_goal') else []
        if self.kind == 'apply_plan_patch':
            records = self.payload.get('upserts', ())
        elif self.kind == 'update_direction':
            records = self.payload.get('goals', ())
        for record in records:
            require(record.get('visibility') != 'source_stream', 'source_stream requires a trusted record source; unsupported for this record')
        object.__setattr__(self, 'payload', freeze_json(self.payload))
        if self.expires_at is not None:
            object.__setattr__(self, 'expires_at', as_utc(self.expires_at))
