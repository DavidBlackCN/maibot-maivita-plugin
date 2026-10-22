"""Time Window Utility Functions.

This module provides utilities for parsing and migrating time windows,
avoiding circular imports and centralizing time-related logic.

The module handles two time window formats:
    - Old format: [hour, hour] where hour is 0-23
    - New format: [minutes, minutes] where minutes is 0-1440

Functions also support overnight time windows (e.g., 23:00-01:00).

Example:
    >>> from time_utils import parse_time_window, format_minutes_to_time
    >>> start, end = parse_time_window([9, 17])  # 9am to 5pm
    >>> print(f"{format_minutes_to_time(start)} - {format_minutes_to_time(end)}")
    09:00 - 17:00
"""

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Iterable, List, Optional, Tuple, Union


MINUTES_PER_DAY = 24 * 60


class TimeParseError(ValueError):
    """Raised when a clock value cannot be parsed without guessing."""


def parse_hhmm(value: str, *, allow_24: bool = False) -> int:
    """Parse a strict ``HH:MM`` value into minutes.

    ``24:00`` is accepted only for interval ends. Invalid values never fall
    back to midnight.
    """
    if not isinstance(value, str) or not value:
        raise TimeParseError("时间不能为空，且必须为 HH:MM")
    if re.fullmatch(r"\d{2}:\d{2}", value) is None:
        raise TimeParseError(f"时间格式错误: {value!r}（应为 HH:MM）")
    hour, minute = (int(part) for part in value.split(":"))
    if allow_24 and hour == 24 and minute == 0:
        return MINUTES_PER_DAY
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        suffix = "；区间结束允许 24:00" if allow_24 else ""
        raise TimeParseError(f"时间非法: {value!r}{suffix}")
    return hour * 60 + minute


@dataclass(frozen=True)
class TimeRange:
    """A half-open interval on a continuous, at-most-two-day timeline."""

    start_minute: int
    end_minute: int
    crosses_midnight: bool = False

    def __post_init__(self) -> None:
        end = self.end_minute
        crosses = self.crosses_midnight or end <= self.start_minute
        if crosses and end <= MINUTES_PER_DAY:
            end += MINUTES_PER_DAY
        if not 0 <= self.start_minute < MINUTES_PER_DAY:
            raise TimeParseError(f"区间开始分钟非法: {self.start_minute}")
        if end <= self.start_minute or end > MINUTES_PER_DAY * 2:
            raise TimeParseError(f"区间结束分钟非法: {end}")
        object.__setattr__(self, "end_minute", end)
        object.__setattr__(self, "crosses_midnight", end > MINUTES_PER_DAY)

    @classmethod
    def from_strings(cls, start: str, end: str) -> "TimeRange":
        start_minute = parse_hhmm(start)
        end_minute = parse_hhmm(end, allow_24=True)
        return cls(start_minute, end_minute, end_minute <= start_minute)

    @property
    def duration_minutes(self) -> int:
        return self.end_minute - self.start_minute

    def segments(self) -> Tuple[Tuple[int, int], ...]:
        if self.end_minute <= MINUTES_PER_DAY:
            return ((self.start_minute, self.end_minute),)
        return (
            (self.start_minute, MINUTES_PER_DAY),
            (0, self.end_minute - MINUTES_PER_DAY),
        )

    def contains_minute(self, minute: int) -> bool:
        minute %= MINUTES_PER_DAY
        return any(start <= minute < end for start, end in self.segments())

    def overlaps(self, other: "TimeRange") -> bool:
        return any(
            max(left_start, right_start) < min(left_end, right_end)
            for left_start, left_end in self.segments()
            for right_start, right_end in other.segments()
        )

    def clamp(self, lower: int = 0, upper: int = MINUTES_PER_DAY * 2) -> "TimeRange":
        start = max(lower, self.start_minute)
        end = min(upper, self.end_minute)
        if end <= start:
            raise TimeParseError("区间 clamp 后为空")
        return TimeRange(start % MINUTES_PER_DAY, end, end > MINUTES_PER_DAY)


def item_time_range(time_slot: str, duration_hours: Any) -> TimeRange:
    """Build an activity range from an LLM schedule item."""
    start = parse_hhmm(time_slot)
    try:
        duration = float(duration_hours)
    except (TypeError, ValueError) as exc:
        raise TimeParseError(f"duration_hours 非法: {duration_hours!r}") from exc
    duration_minutes = round(duration * 60)
    if duration_minutes <= 0:
        raise TimeParseError(f"duration_hours 必须大于 0: {duration_hours!r}")
    return TimeRange(start, start + duration_minutes, start + duration_minutes > MINUTES_PER_DAY)


def ranges_overlap_any(target: TimeRange, ranges: Iterable[TimeRange]) -> bool:
    return any(target.overlaps(other) for other in ranges)


def strip_tz(dt: Optional[datetime]) -> Optional[datetime]:
    """剥离 datetime 的 tzinfo，便于跨 tz-aware / tz-naive 安全比较。

    历史背景：v4.0 时代 ``TimezoneManager`` 用 pytz 或系统时区，``get_now()``
    返回的 datetime 可能是 tz-naive；v4.1 切到 ``zoneinfo`` 后变成 tz-aware。
    数据库里旧数据是 tz-naive 字符串，新数据是 tz-aware 字符串，两者直接比较
    会抛 ``TypeError: can't compare offset-naive and offset-aware datetimes``。

    在所有 ``goal.created_at`` / ``goal.deadline`` 与"现在"的比较点统一剥离 tz，
    保证不会爆错。代价：失去时区精度（但本插件比较都是天级别，影响可忽略）。

    Args:
        dt: 可能带也可能不带 tzinfo 的 datetime；``None`` 直接返回。

    Returns:
        相同时刻的 tz-naive datetime；``None`` 输入返回 ``None``。
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt



def migrate_time_window(time_window: Optional[List[Union[int, float]]]) -> Optional[List[int]]:
    """
    迁移旧格式时间窗口到新格式

    旧格式: [hour, hour] (0-23)
    新格式: [minutes, minutes] (0-1440)

    支持跨夜时间窗口，如 [23, 1] 表示 23:00-次日01:00

    Args:
        time_window: 时间窗口，可能是旧格式或新格式

    Returns:
        新格式的时间窗口，如果输入无效则返回None
    """
    if not time_window or len(time_window) < 2:
        return None

    start, end = time_window[0], time_window[1]

    # 检测无效时间窗口（起止时间相同）
    if start == end:
        import logging
        logger = logging.getLogger("autonomous_planning")
        logger.warning(f"无效的时间窗口: {time_window} (起止时间相同)")
        return None

    # 如果两个值都小于等于24，判定为旧格式（小时）
    if start < 24 and end <= 24:
        start_minutes = start * 60
        end_minutes = end * 60

        # 处理跨夜：如果end <= start，说明跨越了午夜
        # 例如 [23, 1] 应该转换为 [1380, 1500] (次日1点 = 1440 + 60)
        if end_minutes <= start_minutes:
            end_minutes += 1440

        return [start_minutes, end_minutes]

    # 已经是新格式
    return time_window


def parse_time_window(time_window: Optional[List[Union[int, float]]]) -> Tuple[Optional[int], Optional[int]]:
    """
    解析时间窗口，统一返回分钟格式

    Args:
        time_window: 时间窗口（旧格式或新格式）

    Returns:
        (start_minutes, end_minutes) 或 (None, None)
    """
    migrated = migrate_time_window(time_window)
    if not migrated:
        return None, None
    return migrated[0], migrated[1]


def parse_time_slot(time_slot: str) -> Tuple[Optional[int], Optional[int]]:
    """
    解析 HH:MM 格式时间字符串为分钟数

    Args:
        time_slot: 时间字符串，如 "09:30"

    Returns:
        (hour, minute) 或 (None, None)
    """
    try:
        total = parse_hhmm(time_slot)
        return total // 60, total % 60
    except TimeParseError:
        return None, None


def time_slot_to_minutes(time_slot: str) -> Optional[int]:
    """
    将 HH:MM 格式时间转换为从00:00开始的分钟数

    Args:
        time_slot: 时间字符串，如 "09:30"

    Returns:
        分钟数（如 570 表示 09:30）或 None
    """
    hour, minute = parse_time_slot(time_slot)
    if hour is None:
        return None
    return hour * 60 + minute


def format_minutes_to_time(minutes: int) -> str:
    """
    将分钟数格式化为 HH:MM 字符串

    Args:
        minutes: 从00:00开始的分钟数

    Returns:
        格式化的时间字符串，如 "09:30"
    """
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def get_time_window_from_goal(goal: Any) -> Tuple[int, int]:
    """
    从目标对象中提取时间窗口（统一接口）

    优先从 parameters 读取，其次从 conditions 读取

    Args:
        goal: 目标对象

    Returns:
        (start_minutes, end_minutes) 元组，默认返回 (0, 60)
    """
    # 优先从parameters读取time_window，其次从conditions读取
    time_window = None
    if hasattr(goal, 'parameters') and goal.parameters and "time_window" in goal.parameters:
        time_window = goal.parameters.get("time_window")
    elif hasattr(goal, 'conditions') and goal.conditions and "time_window" in goal.conditions:
        time_window = goal.conditions.get("time_window")

    if not time_window:
        return (0, 60)

    start_minutes, end_minutes = parse_time_window(time_window)
    if start_minutes is None:
        return (0, 60)

    return (start_minutes, end_minutes)
