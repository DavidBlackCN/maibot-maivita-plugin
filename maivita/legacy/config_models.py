"""MaiVita config schema 1; R0 projects it to the legacy engine."""
from copy import deepcopy
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import create_model, model_validator
from maibot_sdk import Field, PluginConfigBase
from ..compatibility.legacy_config import (
    ScheduleConfig, ScheduleModelConfig, ScheduleFrequencyConfig,
    ActivityFrequencyFactorsConfig, InjectConfig, AutonomousPlanningConfig,
)
from ..shared.utils.time_utils import TimeRange

SUPPORTED_CONFIG_VERSION = "1"

# Every old schedule field has exactly one canonical location. R2 retires limits.
FIELD_MAP = {
    "timezone": ("character", "timezone"), "custom_prompt": ("character", "life_context"),
    "sleep_start_time": ("compatibility", "legacy_sleep_start_time"),
    "sleep_end_time": ("compatibility", "legacy_sleep_end_time"),
    "no_sleep_mode": ("sleep", "mode"), "model": ("models", "strategic"),
    "frequency": ("compatibility", "frequency"),
    "admin_users": ("admin", "legacy_admin_users"),
    "allowed_streams": ("integration", "allowed_streams"),
    "proactive_streams": ("compatibility", "proactive_streams"),
    "enable_proactive_trigger": ("compatibility", "enable_proactive_trigger"),
    "cross_day_activity": ("compatibility", "cross_day_activity"),
    "min_activities": ("compatibility", "min_activities"),
    "cache_ttl": ("runtime", "cache_ttl"), "cache_max_size": ("runtime", "cache_max_size"),
    "llm_log_enabled": ("observability", "model_logs_enabled"),
    "llm_log_retention_days": ("observability", "model_logs_retention_days"),
    "auto_schedule_enabled": ("planning", "daily_enabled"),
    "auto_schedule_time": ("planning", "daily_trigger_time"),
    "auto_generate": ("planning", "generate_on_request"),
    "auto_infer_next_day_prompt": ("planning", "direction_summary_enabled"),
    "infer_time": ("planning", "direction_time"),
    "infer_lookback_days": ("planning", "direction_lookback_days"),
    "infer_max_prompt_chars": ("planning", "direction_max_prompt_chars"),
    "infer_use_completion_signal": ("planning", "direction_use_completion_signal"),
    "max_activities": ("planning", "max_daily_items"),
    "recent_schedule_days": ("planning", "history_days"),
}
for _key in ("inject_schedule", "inject_into_replyer", "max_future_activities"):
    FIELD_MAP[_key] = ("integration", _key)
for _key in ("max_tokens", "generation_timeout", "generation_max_retries", "generation_retry_backoff"):
    FIELD_MAP[_key] = ("models", _key)
for _key in ScheduleConfig.model_fields:
    FIELD_MAP.setdefault(_key, ("planning", _key))


class StrictSection(PluginConfigBase):
    model_config = {"extra": "forbid"}


def section(name, additions=None):
    fields = {}
    for old, (group, new) in FIELD_MAP.items():
        if group == name and old != "no_sleep_mode":
            field = ScheduleConfig.model_fields[old]
            fields[new] = (field.annotation, deepcopy(field))
    fields.update(additions or {})
    return create_model(name.title() + "Config", __base__=StrictSection, **fields)


class PluginSectionConfig(StrictSection):
    enabled: bool = True
    config_version: Literal["1"] = "1"


CharacterConfig = section("character", {"character_id": (Literal["default"], "default")})
PlanningConfig = section("planning", {"mode": (Literal["legacy"], "legacy")})
SleepConfig = section("sleep", {"mode": (Literal["legacy_fixed", "disabled"], "legacy_fixed")})
RoutineConfig = section("routine", {"preferred_sleep_time": (str, "01:00"), "preferred_wake_time": (str, "08:00")})
ModelsConfig = section("models")
IntegrationConfig = section("integration", {"injection": (InjectConfig, Field(default_factory=InjectConfig))})
AdminConfig = section("admin")
RuntimeConfig = section("runtime")
ObservabilityConfig = section("observability")
CompatibilityConfig = section("compatibility", {"legacy_empty_admin_all": (bool, False)})


class StorageConfig(StrictSection):
    data_dir: str = ""
    writer_lock: str = ""  # Explicit deployment-wide absolute path, no fallback.
    cleanup_interval: int = 3600
    history_detail_days: int = 7


class MaiVitaConfig(StrictSection):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    character: CharacterConfig = Field(default_factory=CharacterConfig)
    routine: RoutineConfig = Field(default_factory=RoutineConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    sleep: SleepConfig = Field(default_factory=SleepConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    compatibility: CompatibilityConfig = Field(default_factory=CompatibilityConfig)

    @model_validator(mode="after")
    def validate_legacy_projection(self):
        try:
            ZoneInfo(self.character.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('unknown character timezone') from exc
        self.schedule
        self.autonomous_planning
        if self.sleep.mode == "legacy_fixed":
            TimeRange.from_strings(self.compatibility.legacy_sleep_start_time,
                                   self.compatibility.legacy_sleep_end_time)
        return self

    @property
    def schedule(self) -> ScheduleConfig:
        data = {old: getattr(getattr(self, group), new) for old, (group, new) in FIELD_MAP.items()}
        data["no_sleep_mode"] = self.sleep.mode == "disabled"
        return ScheduleConfig.model_validate(data)

    @property
    def inject(self) -> InjectConfig:
        return self.integration.injection

    @property
    def autonomous_planning(self) -> AutonomousPlanningConfig:
        return AutonomousPlanningConfig(cleanup_interval=self.storage.cleanup_interval,
                                        cleanup_old_goals_days=self.storage.history_detail_days)


AutonomousPlanningV4Config = MaiVitaConfig
