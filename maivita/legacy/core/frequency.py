"""Pure frequency calculations shared by runtime synchronization and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class FrequencyBreakdown:
    base_frequency: float
    activity_factor: float
    sleep_factor: float
    adjust: float
    actual_frequency: float


def _value(source: Any, name: str, default: Any) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def activity_factor_for(goal_type: str, factors: Any) -> float:
    """Return a configured activity factor; unknown types are neutral."""
    return float(_value(factors, str(goal_type or ""), 1.0))


def calculate_frequency(
    *,
    base_frequency: float,
    goal_type: str,
    sleeping: bool,
    no_sleep_mode: bool,
    frequency_config: Any,
) -> FrequencyBreakdown:
    """Calculate ``base × activity × sleep`` without clamping values above one."""
    sleep_active = (
        bool(_value(frequency_config, "enable_sleep", True))
        and sleeping
        and not no_sleep_mode
    )
    activity_active = bool(_value(frequency_config, "enable_activity", True)) and not sleeping
    factors = _value(frequency_config, "activity_factors", {})
    activity_factor = activity_factor_for(goal_type, factors) if activity_active else 1.0
    sleep_factor = float(_value(frequency_config, "sleep_factor", 0.0)) if sleep_active else 1.0
    adjust = activity_factor * sleep_factor
    base = float(base_frequency)
    return FrequencyBreakdown(base, activity_factor, sleep_factor, adjust, base * adjust)


def parse_rule_range(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if not text or text == "*":
        return None
    try:
        start_text, end_text = (part.strip() for part in text.split("-", 1))
        sh, sm = (int(part) for part in start_text.split(":"))
        eh, em = (int(part) for part in end_text.split(":"))
    except (TypeError, ValueError):
        return None
    if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
        return None
    return sh * 60 + sm, eh * 60 + em


def _target_priority(rule: Mapping[str, Any], stream: Mapping[str, Any]) -> int | None:
    rule_type = str(rule.get("rule_type") or "").strip()
    is_group = bool(
        stream.get("is_group_session")
        or stream.get("is_group")
        or str(stream.get("chat_type") or "").lower() == "group"
        or stream.get("group_id")
    )
    if rule_type not in {"group", "private"} or (rule_type == "group") != is_group:
        return None
    platform = str(rule.get("platform") or "").strip()
    item_id = str(rule.get("item_id") or "").strip()
    if not platform and not item_id:
        return 1
    stream_platform = str(stream.get("platform") or "").strip()
    stream_item = str(stream.get("group_id") if is_group else stream.get("user_id") or "").strip()
    platform_match = not platform or platform == "*" or platform == stream_platform
    item_match = not item_id or item_id == "*" or item_id == stream_item
    if not platform_match or not item_match:
        return None
    if platform not in {"", "*"} and item_id not in {"", "*"}:
        return 5
    if platform == "*" or item_id == "*":
        return 4
    return 3


def _time_priority(rule_time: Any, minute: int) -> int | None:
    text = str(rule_time or "").strip()
    if not text:
        return 1
    if text == "*":
        return 3
    parsed = parse_rule_range(text)
    if parsed is None:
        return None
    start, end = parsed
    current = minute % 1440
    matches = start <= current <= end if start <= end else current >= start or current <= end
    return 2 if matches else None


def base_frequency_at(
    default_base: float,
    rules: Iterable[Mapping[str, Any]],
    *,
    minute: int,
    stream: Mapping[str, Any],
    rules_enabled: bool,
) -> float:
    """Mirror MaiBot's target/time priority selection for a given minute."""
    if not rules_enabled:
        return float(default_base)
    selected_priority = (0, 0)
    selected_value: float | None = None
    for rule in rules:
        target = _target_priority(rule, stream)
        timing = _time_priority(rule.get("time"), minute)
        if target is None or timing is None or (target, timing) <= selected_priority:
            continue
        try:
            value = float(rule.get("value"))
        except (TypeError, ValueError):
            continue
        selected_priority = (target, timing)
        selected_value = value
    return float(default_base) if selected_value is None else selected_value


def rule_boundaries(rules: Iterable[Mapping[str, Any]]) -> set[int]:
    """Return minute boundaries where an inclusive MaiBot time rule may change."""
    boundaries = {0, 1440}
    for rule in rules:
        parsed = parse_rule_range(rule.get("time"))
        if parsed is None:
            continue
        start, end = parsed
        boundaries.add(start)
        boundaries.add((end + 1) % 1440)
    return boundaries
