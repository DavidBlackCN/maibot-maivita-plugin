"""Explicit conversion; reports paths/actions only, never private raw values."""
from copy import deepcopy
from ..config_models import MaiVitaConfig, FIELD_MAP
from .legacy_config import AutonomousPlanningV4Config as LegacyConfig


def convert_legacy(raw):
    if str(raw.get("plugin", {}).get("config_version", "")) != "5.1.0":
        raise ValueError("only verified v5.1.0 configs can be converted")
    unknown = []
    def check(model, data, prefix=""):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            field = model.model_fields.get(key)
            if field is None:
                unknown.append(path)
            elif isinstance(value, dict) and hasattr(field.annotation, "model_fields"):
                check(field.annotation, value, path)
    check(LegacyConfig, raw)
    if unknown:
        return None, {"status": "blocked_unknown_fields", "unknown_paths": sorted(unknown), "source_preserved": True}
    legacy = LegacyConfig.model_validate(deepcopy(raw))
    data = MaiVitaConfig().model_dump()
    data["plugin"]["enabled"] = legacy.plugin.enabled
    mappings = [{"source": "plugin.config_version", "target": "plugin.config_version", "action": "converted_to_1"}]
    for key, (group, new) in FIELD_MAP.items():
        value = legacy.schedule.model_dump()[key]
        data[group][new] = ("disabled" if value else "legacy_fixed") if key == "no_sleep_mode" else value
        mappings.append({"source": "schedule." + key, "target": group + "." + new, "action": "preserved_R0"})
    data["routine"].update(preferred_sleep_time=legacy.schedule.sleep_start_time, preferred_wake_time=legacy.schedule.sleep_end_time)
    data["integration"]["injection"] = legacy.inject.model_dump()
    data["storage"].update(cleanup_interval=legacy.autonomous_planning.cleanup_interval, history_detail_days=legacy.autonomous_planning.cleanup_old_goals_days)
    data["compatibility"]["legacy_empty_admin_all"] = not legacy.schedule.admin_users
    for key in type(legacy.inject).model_fields:
        mappings.append({"source": "inject." + key, "target": "integration.injection." + key, "action": "preserved_R0"})
    for old, new in [("cleanup_interval", "cleanup_interval"), ("cleanup_old_goals_days", "history_detail_days")]:
        mappings.append({"source": "autonomous_planning." + old, "target": "storage." + new, "action": "preserved_R0"})
    mappings.append({"source": "plugin.enabled", "target": "plugin.enabled", "action": "preserved_R0"})
    validated = MaiVitaConfig.model_validate(data)
    return validated.model_dump(), {"status": "converted", "mappings": mappings,
        "diagnostics": (["legacy_empty_admin_all: all users retain /plan access; review before R2"] if not legacy.schedule.admin_users else []),
        "activation": "not_performed; configure shared storage.writer_lock and confirm old plugin stopped"}
