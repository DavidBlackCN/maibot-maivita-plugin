"""Deterministic normalization and hard constraints for daily schedules."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.activity_state import (
    ActivityState,
    PUBLIC_SLEEP_NAME,
    contains_internal_activity_terms,
    is_locked_sleep,
)
from ..utils.time_utils import TimeParseError, TimeRange, item_time_range


SLEEP_KEYWORDS = ("睡觉", "睡眠", "安睡", "入睡", "就寝", "夜间睡眠", "补觉", "小憩")


@dataclass
class NormalizationResult:
    items: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def is_sleep_like(item: Dict[str, Any]) -> bool:
    if is_locked_sleep(item.get("parameters")):
        return True
    name = str(item.get("name") or "")
    return any(keyword in name for keyword in SLEEP_KEYWORDS)


def make_locked_sleep_item(sleep_window: TimeRange, description: str) -> Dict[str, Any]:
    return {
        "name": PUBLIC_SLEEP_NAME,
        "description": description,
        "goal_type": "daily_routine",
        "priority": "high",
        "time_slot": f"{sleep_window.start_minute // 60:02d}:{sleep_window.start_minute % 60:02d}",
        "duration_hours": sleep_window.duration_minutes / 60,
        "parameters": {
            "system_activity": True,
            "activity_state": ActivityState.SLEEPING.value,
            "locked": True,
            "time_window": [sleep_window.start_minute, sleep_window.end_minute],
        },
        "conditions": {},
    }


def normalize_daily_schedule(
    items: List[Dict[str, Any]],
    *,
    sleep_window: Optional[TimeRange],
    no_sleep_mode: bool,
) -> NormalizationResult:
    """保留 LLM 睡眠描述，同时由程序强制规范睡眠结构和时间。"""
    result = NormalizationResult()
    sleep_descriptions: List[str] = []
    for index, original in enumerate(items):
        item = dict(original)
        item["parameters"] = dict(item.get("parameters") or {})
        item["conditions"] = dict(item.get("conditions") or {})
        if no_sleep_mode and is_sleep_like(item):
            item.update(
                name="无所事事",
                description="放空休息，享受没有安排的时间",
                goal_type="free_time",
            )
            item["parameters"].pop("activity_state", None)
            item["parameters"].pop("locked", None)
            item["parameters"].pop("system_activity", None)
            result.warnings.append(f"第{index + 1}项睡眠活动已在 no_sleep_mode 下转为空闲时间")
        elif sleep_window is not None:
            if is_sleep_like(item):
                description = str(item.get("description") or "").strip()
                sleep_descriptions.append(description)
                continue
            try:
                block = item_time_range(item.get("time_slot"), item.get("duration_hours"))
            except TimeParseError:
                # Validator reports a precise ERROR; do not guess or repair malformed time.
                result.items.append(item)
                continue
            if block.overlaps(sleep_window):
                result.warnings.append(
                    f"第{index + 1}项 {item.get('name', '未命名')} 侵入固定睡眠窗口，已移除"
                )
                continue
        result.items.append(item)

    if sleep_window is not None and not no_sleep_mode:
        if len(sleep_descriptions) != 1:
            result.errors.append(
                f"启用睡眠时必须生成且只生成一条睡觉活动，当前为 {len(sleep_descriptions)} 条"
            )
        description = sleep_descriptions[0] if sleep_descriptions else ""
        if not description:
            result.errors.append("睡觉活动 description 不能为空")
        elif contains_internal_activity_terms(description):
            result.errors.append("睡觉活动 description 包含内部实现术语")
            description = ""
        result.items.append(make_locked_sleep_item(sleep_window, description))

    def sort_key(item: Dict[str, Any]) -> int:
        try:
            return item_time_range(item.get("time_slot"), item.get("duration_hours")).start_minute
        except TimeParseError:
            return 10**9

    result.items.sort(key=sort_key)
    return result
