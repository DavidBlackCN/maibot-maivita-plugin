"""Five-layer prompt builder; arithmetic and sleep enforcement stay in code."""

import json
import logging
from typing import Any, Dict, List, Optional

from ....shared.utils.time_utils import TimeRange
from ....shared.utils.timezone_manager import TimezoneManager

logger = logging.getLogger(__name__)


class PromptBuilder:
    def __init__(self, config: Dict[str, Any], tz_manager: TimezoneManager):
        self.config = config
        self.tz_manager = tz_manager

    def _get_cached_config(self) -> tuple[str, str, str, str]:
        profile = self.config.get("bot_profile", {}) or {}
        values = tuple(str(profile.get(key) or "").strip() for key in (
            "personality", "reply_style", "interest", "bot_name"
        ))
        if not values[0]:
            logger.error("bot_profile.personality 为空，日程的人设贴合度可能下降")
        return values  # type: ignore[return-value]

    def _sleep_window(self) -> Optional[TimeRange]:
        if self.config.get("no_sleep_mode", False):
            return None
        start = str(self.config.get("sleep_start_time") or "").strip()
        end = str(self.config.get("sleep_end_time") or "").strip()
        return TimeRange.from_strings(start, end) if start and end else None

    def build_schedule_prompt(
        self,
        schedule_type: str,
        preferences: Dict[str, Any],
        schema: Optional[Dict] = None,
        yesterday_context: Optional[str] = None,
        pending_commitments: Optional[List[Dict[str, Any]]] = None,
        history_context: str = "",
        knowledge_context: str = "",
    ) -> str:
        personality, reply_style, interest, bot_name = self._get_cached_config()
        now = self.tz_manager.get_now()
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        min_items = int(self.config.get("min_activities", 8))
        max_items = int(self.config.get("max_activities", 15))
        custom_prompt = str(self.config.get("custom_prompt") or "").strip()
        no_sleep = bool(self.config.get("no_sleep_mode", False))
        sleep = self._sleep_window()

        commitment_lines = []
        for item in pending_commitments or []:
            commitment_lines.append(
                f"- {item.get('time') or '时间待定'} {item.get('title') or '未命名约定'}"
                + (f"（{item.get('notes')}）" if item.get("notes") else "")
            )

        hard = [
            "下列日程条件必须同时满足；睡眠时间和已确认约定不可改动。",
            "time_slot 必须为严格 HH:MM；duration_hours 必须大于 0。",
            "不要生成互相重叠的活动。空档是允许的，可用 free_time/rest/idle/commute 表达自然生活。",
        ]
        if sleep:
            start = f"{sleep.start_minute // 60:02d}:{sleep.start_minute % 60:02d}"
            end_mod = sleep.end_minute % 1440
            end = "24:00" if sleep.end_minute == 1440 else f"{end_mod // 60:02d}:{end_mod % 60:02d}"
            hard.append(
                f"必须生成且只生成一条名为“睡觉”的活动；其时间必须为 {start}-{end}，"
                "description 必须结合角色人设自然描写睡眠状态，不得为空，也不得提及任何程序实现。"
            )
            hard.append(f"除“睡觉”外，不要生成任何与 {start}-{end} 重叠的活动。")
        elif no_sleep:
            hard.append("no_sleep_mode 已开启：不要生成睡眠、补觉或小憩活动；也没有睡眠时间限制。")

        role = f"角色名：{bot_name or '未提供'}\n人设：{personality or '按普通自然生活处理'}\n兴趣：{interest or '未提供'}"
        if reply_style:
            role += f"\n表达风格（仅轻度影响 description，不决定日程结构）：{reply_style}"
        context_parts = [
            f"日期：{now:%Y-%m-%d} {weekday}",
            f"长期生活阶段：{custom_prompt or '无额外说明'}",
            f"最近几天日程：{yesterday_context or '无'}",
            f"[Hard Commitments]\n" + ("\n".join(commitment_lines) if commitment_lines else "无"),
            f"[Soft Signals / Recent Context]\n{history_context or '无'}",
            f"[Mood / State / Memory]\n{knowledge_context or '无'}",
        ]
        desc = (
            f"description 用自然日记式表达，建议 {self.config.get('min_description_length', 20)}-"
            f"{self.config.get('max_description_length', 50)} 字。"
            if self.config.get("enable_detailed_description", True)
            else (
                "除“睡觉”外的活动 description 填空字符串；“睡觉”仍必须填写"
                "符合人设的自然睡眠描述。"
            )
        )
        contract = {
            "schedule_items": [{
                "name": "活动名称", "description": "活动描述", "goal_type": "study",
                "priority": "medium", "time_slot": "09:00", "duration_hours": 1.5,
            }]
        }
        schema_text = ""
        if schema:
            schema_text = "\nSchema: " + json.dumps(
                schema.get("properties", {}).get("schedule_items", {}), ensure_ascii=False
            )

        return f"""[1. ROLE]
{role}

[2. TODAY CONTEXT]
{chr(10).join(context_parts)}

[3. HARD CONSTRAINTS]
{chr(10).join(f'- {line}' for line in hard)}

[4. GENERATION GOAL]
生成 {schedule_type} 日程，使今天像真实生活而不是无缝甘特图。保留稳定习惯，但具体内容不要机械重复最近几天；改变实际内容而非只改名字。结合人设、兴趣、长期状态和近期事件。{desc}

[5. OUTPUT CONTRACT]
仅输出严格 JSON。生成 {min_items}-{max_items} 个活动。goal_type 可用 daily_routine/meal/study/entertainment/social_maintenance/exercise/learn_topic/rest/free_time/custom；priority 仅 high/medium/low。
格式示例（只展示字段，不代表完整一天）：
{json.dumps(contract, ensure_ascii=False, indent=2)}{schema_text}
"""

    def build_retry_prompt(
        self,
        schedule_type: str,
        preferences: Dict[str, Any],
        schema: Dict,
        previous_issues: List[str],
        yesterday_context: Optional[str] = None,
        pending_commitments: Optional[List[Dict[str, Any]]] = None,
        history_context: str = "",
        knowledge_context: str = "",
    ) -> str:
        base = self.build_schedule_prompt(
            schedule_type, preferences, schema, yesterday_context, pending_commitments,
            history_context, knowledge_context,
        )
        errors = [issue.removeprefix("ERROR: ") for issue in previous_issues if issue.startswith("ERROR: ")]
        warnings = [issue.removeprefix("WARNING: ") for issue in previous_issues if issue.startswith("WARNING: ")][:5]
        commitments = [
            f"- {item.get('time') or '时间待定'} {item.get('title') or '未命名约定'}"
            for item in pending_commitments or []
        ]
        return base + "\n上一版存在：\nERROR:\n" + (
            "\n".join(f"- {item}" for item in errors) or "- 无"
        ) + "\nWARNING:\n" + (
            "\n".join(f"- {item}" for item in warnings) or "- 无"
        ) + "\n必须保留：\n" + (
            "\n".join(commitments) or "- 配置的睡眠时间，以及一条符合人设的自然睡眠描述"
        ) + "\n请重新输出完整 JSON。\n"
