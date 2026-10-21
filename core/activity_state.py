"""Canonical activity-state helpers shared by scheduling and runtime services."""

from enum import Enum
from typing import Any, Mapping


PUBLIC_SLEEP_NAME = "睡觉"
PUBLIC_SLEEP_DESCRIPTION = ""
_INTERNAL_ACTIVITY_TERMS = (
    "系统锁定",
    "固定睡眠块",
    "硬约束",
    "system activity",
    "system_activity",
    "locked",
    "hard constraint",
    "fixed sleep",
)


class ActivityState(str, Enum):
    SLEEPING = "sleeping"
    ACTIVE = "active"
    RESTING = "resting"
    FREE = "free"
    AWAKE = "awake"


def activity_state_from_parameters(parameters: Mapping[str, Any] | None) -> ActivityState:
    raw = str((parameters or {}).get("activity_state") or "").lower()
    try:
        return ActivityState(raw)
    except ValueError:
        return ActivityState.AWAKE


def is_locked_sleep(parameters: Mapping[str, Any] | None) -> bool:
    params = parameters or {}
    return (
        activity_state_from_parameters(params) is ActivityState.SLEEPING
        and bool(params.get("system_activity"))
        and bool(params.get("locked"))
    )


def public_activity_text(
    name: Any,
    description: Any,
    parameters: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """返回适合展示的活动文案，同时隐藏内部睡眠实现术语。

    历史数据库可能仍保存旧版机械描述，因此所有展示入口都应通过此函数。
    内部参数不会被修改。
    """
    if is_locked_sleep(parameters):
        public_description = str(description or "").strip()
        if contains_internal_activity_terms(public_description):
            public_description = ""
        return PUBLIC_SLEEP_NAME, public_description
    return str(name or ""), str(description or "")


def contains_internal_activity_terms(value: Any) -> bool:
    """判断用户可见文本是否包含睡眠块内部实现术语。"""
    lowered = str(value or "").lower()
    return any(term in lowered for term in _INTERNAL_ACTIVITY_TERMS)


def public_error_text(error: Any, fallback: str = "日程处理未完成，请查看日志") -> str:
    """避免内部睡眠校验术语通过异常消息进入聊天输出。"""
    text = str(error or "").strip()
    if contains_internal_activity_terms(text):
        return fallback
    return text or fallback
