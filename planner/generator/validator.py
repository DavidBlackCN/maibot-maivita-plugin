"""Structural and semantic validation for generated schedules."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...core.activity_state import is_locked_sleep
from ...utils.time_utils import TimeParseError, TimeRange, item_time_range, parse_hhmm


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    items: List[Dict[str, Any]] = field(default_factory=list)


class ScheduleSemanticValidator:
    """Separate blocking structural errors from advisory lifestyle warnings."""

    def validate(
        self,
        items: List[Dict[str, Any]],
        *,
        sleep_window: Optional[TimeRange] = None,
        no_sleep_mode: bool = False,
        required_commitments: Optional[List[Dict[str, Any]]] = None,
    ) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []
        parsed: List[tuple[int, Dict[str, Any], TimeRange]] = []
        locked_sleep: List[tuple[int, Dict[str, Any], TimeRange]] = []

        for index, item in enumerate(items):
            label = f"第{index + 1}项 ({item.get('name', '未命名')})"
            try:
                block = item_time_range(item.get("time_slot"), item.get("duration_hours"))
            except TimeParseError as exc:
                errors.append(f"{label}: {exc}")
                continue
            parsed.append((index, item, block))
            if is_locked_sleep(item.get("parameters")):
                locked_sleep.append((index, item, block))
            self._append_soft_warnings(item, block, label, warnings)

        for left_pos, (left_idx, left_item, left) in enumerate(parsed):
            for right_idx, right_item, right in parsed[left_pos + 1:]:
                if left.overlaps(right):
                    errors.append(
                        f"活动重叠: 第{left_idx + 1}项 {left_item.get('name')} 与"
                        f"第{right_idx + 1}项 {right_item.get('name')}"
                    )

        if no_sleep_mode:
            if locked_sleep:
                errors.append("no_sleep_mode 下不得存在系统锁定睡眠块")
        elif sleep_window is not None:
            if len(locked_sleep) != 1:
                errors.append(f"固定睡眠块数量必须为 1，当前为 {len(locked_sleep)}")
            elif locked_sleep[0][2] != sleep_window:
                errors.append("固定睡眠窗口缺失或覆盖不完整")
            for _, item, block in parsed:
                if not is_locked_sleep(item.get("parameters")) and block.overlaps(sleep_window):
                    errors.append(f"活动 {item.get('name', '未命名')} 侵入固定睡眠窗口")

        self._validate_commitments(items, required_commitments or [], sleep_window, errors)
        return ValidationResult(not errors, errors, warnings, list(items))

    @staticmethod
    def _validate_commitments(
        items: List[Dict[str, Any]],
        commitments: List[Dict[str, Any]],
        sleep_window: Optional[TimeRange],
        errors: List[str],
    ) -> None:
        haystacks = [f"{item.get('name', '')} {item.get('description', '')}" for item in items]
        for commitment in commitments:
            title = str(commitment.get("title") or "").strip()
            time_value = str(commitment.get("time") or "").strip()
            if time_value and sleep_window is not None:
                try:
                    if sleep_window.contains_minute(parse_hhmm(time_value)):
                        errors.append(f"已确认约定 {title or time_value} 位于固定睡眠窗口内")
                        continue
                except TimeParseError as exc:
                    errors.append(f"约定 {title or '未命名'} 时间非法: {exc}")
                    continue
            if title and not any(title in text for text in haystacks):
                errors.append(f"必须保留的 commitment 丢失: {title}")

    @staticmethod
    def _append_soft_warnings(
        item: Dict[str, Any], block: TimeRange, label: str, warnings: List[str]
    ) -> None:
        name = str(item.get("name") or "")
        goal_type = str(item.get("goal_type") or "")
        hour = block.start_minute // 60
        if goal_type == "meal":
            ranges = {"早餐": (6, 10), "午餐": (11, 15), "晚餐": (17, 21)}
            for meal, (start, end) in ranges.items():
                if meal in name and not start <= hour <= end:
                    warnings.append(f"{label}: {meal}时间较少见")
        if block.duration_minutes > 180 and not any(word in name for word in ("自由", "休息", "睡")):
            warnings.append(f"{label}: 连续活动超过 3 小时")
