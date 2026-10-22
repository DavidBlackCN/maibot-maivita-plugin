"""MaiBot 具体模型固定调用适配层。

该模块集中封装对 MaiBot Host 内部 ``src.*`` API 的依赖。业务代码只调用
``generate_with_model_selection``，后续 Host API 变化时仅需调整本模块。
"""

from __future__ import annotations

import asyncio
import logging
import tomllib
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from ..core.exceptions import LLMError

if TYPE_CHECKING:
    from .model_selection import ScheduleModelSelection


logger = logging.getLogger(__name__)


class ConfiguredModelNotFoundError(LLMError):
    """配置指定的模型无法从 MaiBot model_config.toml 解析。"""


def _find_model_config_path(plugin_root: Path | str | None) -> Path:
    """按标准插件目录结构定位 Host 的 ``config/model_config.toml``。"""
    if plugin_root is not None:
        root = Path(plugin_root).resolve()
        candidate = root.parent.parent / "config" / "model_config.toml"
        if candidate.is_file():
            return candidate

    # 当前文件位于 <host>/plugins/<plugin>/maivita/legacy/planner/ 下。
    candidate = Path(__file__).resolve().parents[5] / "config" / "model_config.toml"
    if candidate.is_file():
        return candidate
    raise LLMError(
        "无法定位 MaiBot config/model_config.toml，不能解析 schedule.model.model，"
        "已拒绝生成"
    )


def _load_model_config(path: Path) -> Mapping[str, Any]:
    """读取 Host 模型配置，不缓存错误结果。"""
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LLMError(f"读取 MaiBot model_config.toml 失败，已拒绝生成: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("MaiBot model_config.toml 结构无效，已拒绝生成")
    return data


def resolve_model_name(configured_model: str, model_config: Mapping[str, Any]) -> str:
    """把 ``models[].name`` 或 ``model_identifier`` 解析为唯一的模型名称。"""
    configured = str(configured_model or "").strip()
    raw_models = model_config.get("models")
    models = [item for item in raw_models if isinstance(item, Mapping)] if isinstance(raw_models, list) else []

    # alias/name 优先，避免某个 identifier 恰好与另一个模型别名相同。
    by_name = [
        str(item.get("name") or "").strip()
        for item in models
        if str(item.get("name") or "").strip() == configured
    ]
    if by_name:
        return by_name[0]

    by_identifier = [
        str(item.get("name") or "").strip()
        for item in models
        if str(item.get("model_identifier") or "").strip() == configured
        and str(item.get("name") or "").strip()
    ]
    unique_matches = list(dict.fromkeys(by_identifier))
    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(unique_matches) > 1:
        raise ConfiguredModelNotFoundError(
            f"schedule.model.model 的 model_identifier 存在多个别名，无法唯一解析: "
            f"{configured}；匹配模型: {', '.join(unique_matches)}"
        )

    available = sorted(
        str(item.get("name") or "").strip()
        for item in models
        if str(item.get("name") or "").strip()
    )
    available_text = ", ".join(available) if available else "（无已配置模型）"
    raise ConfiguredModelNotFoundError(
        f"schedule.model.model 指定的模型不存在: {configured}；"
        f"model_config.toml 当前模型: {available_text}"
    )


def _load_host_runtime() -> tuple[type[Any], type[Any]]:
    """延迟导入 Host 内部类型，避免内部依赖扩散到其他模块。"""
    try:
        from src.config.model_configs import TaskConfig
        from src.llm_models.utils_model import LLMOrchestrator
    except (ImportError, AttributeError) as exc:
        raise LLMError(
            "当前 MaiBot Host 缺少固定模型调用所需的 TaskConfig/LLMOrchestrator，"
            "请检查 Host 版本兼容性"
        ) from exc
    return TaskConfig, LLMOrchestrator


async def _generate_with_pinned_model(
    prompt: str,
    *,
    configured_model: str,
    resolved_model: str,
    temperature: float,
    max_tokens: int,
    timeout_ms: int | None,
    request_type: str,
) -> dict[str, Any]:
    """通过单模型 TaskConfig 调用 Host 底层编排器。"""
    TaskConfig, LLMOrchestrator = _load_host_runtime()

    class _PinnedTaskLLMOrchestrator(LLMOrchestrator):
        """始终返回插件提供的单模型任务配置。"""

        def __init__(self, task_config: Any) -> None:
            self._pinned_task_config = task_config
            super().__init__(task_name="planner", request_type=request_type)

        def _get_task_config_or_raise(self) -> Any:
            return self._pinned_task_config

        def _refresh_task_config(self) -> Any:
            latest = self._pinned_task_config
            if latest is not self.model_for_task:
                self.model_for_task = latest
            if list(self.model_usage.keys()) != latest.model_list:
                self.model_usage = {
                    model: self.model_usage.get(model, (0, 0, 0))
                    for model in latest.model_list
                }
            return self.model_for_task

    timeout_seconds = (
        max(float(timeout_ms) / 1000.0, 1.0) if timeout_ms is not None else 240.0
    )
    task_kwargs: dict[str, Any] = {
        "model_list": [resolved_model],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "slow_threshold": min(30.0, timeout_seconds),
        "selection_strategy": "sequential",
    }
    task_fields = getattr(TaskConfig, "model_fields", None) or getattr(
        TaskConfig, "__fields__", {}
    )
    if "hard_timeout" in task_fields:
        task_kwargs["hard_timeout"] = timeout_seconds
    task_config = TaskConfig(**task_kwargs)
    orchestrator = _PinnedTaskLLMOrchestrator(task_config)
    generation = orchestrator.generate_response_async(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    result = await asyncio.wait_for(generation, timeout=timeout_seconds) if timeout_ms else await generation
    payload = {
        "success": True,
        "response": result.response,
        "reasoning": result.reasoning,
        "model_name": result.model_name,
        "model": result.model_name,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "configured_model": configured_model,
        "resolved_model": resolved_model,
    }
    logger.info(
        "model_mode=model configured_model=%s resolved_model=%s actual_model=%s "
        "response_chars=%d reasoning_chars=%d",
        configured_model,
        resolved_model,
        result.model_name,
        len(str(result.response or "")),
        len(str(result.reasoning or "")),
    )
    return payload


async def generate_with_model_selection(
    plugin: Any,
    selection: "ScheduleModelSelection",
    *,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout_ms: int | None = None,
    request_type: str,
) -> dict[str, Any]:
    """按统一配置执行 task 路由或具体模型固定调用。"""
    if selection.mode == "task":
        return await plugin.ctx.llm.generate(
            prompt=prompt,
            model=selection.task,
            temperature=temperature,
            max_tokens=max_tokens,
            **({"timeout_ms": timeout_ms} if timeout_ms is not None else {}),
        )

    config_override = getattr(plugin, "_model_config_path", None)
    config_path = Path(config_override) if config_override else _find_model_config_path(
        getattr(plugin, "_plugin_root", None)
    )
    resolved_model = resolve_model_name(selection.model, _load_model_config(config_path))
    logger.info(
        "model_mode=model configured_model=%s resolved_model=%s actual_model=<pending>",
        selection.model,
        resolved_model,
    )
    result = await _generate_with_pinned_model(
        prompt,
        configured_model=selection.model,
        resolved_model=resolved_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_ms=timeout_ms,
        request_type=request_type,
    )
    selection.validate_result(result)
    return result
