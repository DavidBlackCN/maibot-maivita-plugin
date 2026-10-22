"""日程生成使用的 Maibot 模型选择配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .pinned_model_adapter import ConfiguredModelNotFoundError


DEFAULT_MODEL_TASK = "replyer"


@dataclass(frozen=True)
class ScheduleModelSelection:
    """统一描述 task 路由或具体模型选择。"""

    mode: str = "task"
    task: str = DEFAULT_MODEL_TASK
    model: str = ""

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "ScheduleModelSelection":
        raw = config or {}
        mode = str(raw.get("model_mode", raw.get("mode", "task")) or "task").strip().lower()
        task = str(raw.get("model_task", raw.get("task", DEFAULT_MODEL_TASK)) or "").strip()
        model = str(raw.get("model_name", raw.get("model", "")) or "").strip()
        if mode not in {"task", "model"}:
            raise ValueError(f"schedule.model.mode 只能是 task 或 model，当前值: {mode}")
        if mode == "task" and not task:
            raise ValueError("schedule.model.task 在 mode=task 时不能为空")
        if mode == "model" and not model:
            raise ValueError("schedule.model.model 在 mode=model 时不能为空")
        return cls(mode=mode, task=task or DEFAULT_MODEL_TASK, model=model)

    @property
    def value(self) -> str:
        """返回当前选择的任务名或具体模型名，用于日志与归档。"""
        return self.task if self.mode == "task" else self.model

    @property
    def log_fields(self) -> str:
        if self.mode == "task":
            return f"model_mode=task task={self.task}"
        return f"model_mode=model model={self.model}"

    def validate_result(self, result: Mapping[str, Any]) -> None:
        """防御性确认底层编排器确实使用了解析后的固定模型。"""
        if self.mode != "model" or not bool(result.get("success", False)):
            return
        expected = str(result.get("resolved_model") or self.model).strip()
        actual = str(result.get("model_name") or result.get("model") or "").strip()
        if actual != expected:
            actual_text = actual or "（Host 未返回实际模型名）"
            raise ConfiguredModelNotFoundError(
                f"固定模型调用结果不符: configured_model={self.model}；"
                f"resolved_model={expected}；actual_model={actual_text}；已拒绝本次生成"
            )
