"""数据模型 - 自主规划插件

本模块定义了插件中使用的核心数据模型。

主要类：
    - ScheduleItem: 日程项
    - Schedule: 完整日程
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .constants import ScheduleType as ScheduleTypeEnum


class ScheduleType(Enum):
    """日程类型枚举"""
    DAILY = ScheduleTypeEnum.DAILY
    WEEKLY = ScheduleTypeEnum.WEEKLY
    MONTHLY = ScheduleTypeEnum.MONTHLY


class ScheduleItem:
    """日程项数据模型

    表示单个计划活动，包含名称、描述、时间等信息。

    参数:
        name: 活动名称
        description: 活动描述
        goal_type: 目标类型（如daily_routine、meal等）
        priority: 优先级（high/medium/low）
        time_slot: 时间段（如"09:00"）
        duration_hours: 活动持续时长（小时）
        parameters: 额外参数字典
        conditions: 执行条件字典

    示例:
        >>> item = ScheduleItem(
        ...     name="早餐",
        ...     description="简单吃了点东西",
        ...     goal_type="meal",
        ...     priority="medium",
        ...     time_slot="08:00",
        ...     duration_hours=0.5
        ... )
    """

    def __init__(
        self,
        name: str,
        description: str,
        goal_type: str,
        priority: str,
        time_slot: Optional[str] = None,
        duration_hours: Optional[float] = None,
        parameters: Optional[Dict[str, Any]] = None,
        conditions: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.goal_type = goal_type
        self.priority = priority
        self.time_slot = time_slot
        self.duration_hours = duration_hours
        self.parameters = parameters or {}
        self.conditions = conditions or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式

        Returns:
            包含所有字段的字典
        """
        return {
            "name": self.name,
            "description": self.description,
            "goal_type": self.goal_type,
            "priority": self.priority,
            "time_slot": self.time_slot,
            "duration_hours": self.duration_hours,
            "parameters": self.parameters,
            "conditions": self.conditions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleItem":
        """从字典创建实例

        Args:
            data: 包含字段的字典

        Returns:
            ScheduleItem实例
        """
        return cls(
            name=data["name"],
            description=data["description"],
            goal_type=data["goal_type"],
            priority=data["priority"],
            time_slot=data.get("time_slot"),
            duration_hours=data.get("duration_hours"),
            parameters=data.get("parameters"),
            conditions=data.get("conditions"),
        )

    def __repr__(self) -> str:
        return (
            f"ScheduleItem(name={self.name!r}, "
            f"time_slot={self.time_slot!r}, "
            f"duration={self.duration_hours}h)"
        )


class Schedule:
    """完整日程数据模型

    表示一个完整的日程计划，包含多个日程项。

    参数:
        schedule_type: 日程类型（daily/weekly/monthly）
        name: 日程名称
        items: 日程项列表
        created_at: 创建时间
        metadata: 元数据字典

    示例:
        >>> schedule = Schedule(
        ...     schedule_type=ScheduleType.DAILY,
        ...     name="每日计划 - 2024-01-01",
        ...     items=[item1, item2, item3]
        ... )
    """

    def __init__(
        self,
        schedule_type: ScheduleType,
        name: str,
        items: List[ScheduleItem],
        created_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timezone: str = "Asia/Shanghai",
    ):
        self.schedule_type = schedule_type
        self.name = name
        self.items = items
        # 使用时区感知时间
        if created_at is None:
            from ...shared.utils.timezone_manager import TimezoneManager
            tz_manager = TimezoneManager(timezone)
            created_at = tz_manager.get_now()
        self.created_at = created_at
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式

        Returns:
            包含所有字段的字典
        """
        return {
            "schedule_type": self.schedule_type.value,
            "name": self.name,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Schedule":
        """从字典创建实例

        Args:
            data: 包含字段的字典

        Returns:
            Schedule实例
        """
        return cls(
            schedule_type=ScheduleType(data["schedule_type"]),
            name=data["name"],
            items=[ScheduleItem.from_dict(item) for item in data["items"]],
            created_at=datetime.fromisoformat(data["created_at"]),
            metadata=data.get("metadata"),
        )

    def get_summary(self) -> str:
        """获取日程摘要

        Returns:
            格式化的日程摘要文本
        """
        lines = [
            f"📅 {self.name}",
            f"类型: {self.schedule_type.value}",
            f"任务数: {len(self.items)}",
            ""
        ]

        for i, item in enumerate(self.items, 1):
            time_info = f" @ {item.time_slot}" if item.time_slot else ""
            duration_info = f" (持续{item.duration_hours}小时)" if item.duration_hours else ""
            lines.append(f"{i}. {item.name}{time_info}{duration_info}")
            lines.append(f"   {item.description}")
            lines.append("")

        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"Schedule(type={self.schedule_type.value}, "
            f"name={self.name!r}, "
            f"items={len(self.items)})"
        )

    def __len__(self) -> int:
        """返回日程项数量"""
        return len(self.items)
