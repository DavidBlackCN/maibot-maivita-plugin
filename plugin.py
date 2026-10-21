"""麦麦自主规划插件 v4 - 主入口

完整 7 个组件外壳，业务逻辑下沉到 ``services/``。

组件总览：
- 4 个 ``@Tool``      ：manage_goal_v4 / get_planning_status_v4 / generate_schedule_v4 / apply_schedule_v4
- 1 个 ``@Command``   ：planning_v4 (``/plan`` 或 ``/规划``)
- 1 个 ``@EventHandler`` ：autonomous_planner_v4 (ON_START)
- 1 个 ``@HookHandler``  ：schedule_inject_v4 (maisaka.planner.before_request) ⭐ 注入入口
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple
import asyncio
import logging

from maibot_sdk import API, Command, EventHandler, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import EventType, HookMode, HookOrder, ToolParameterInfo, ToolParamType

from .config_models import MaiVitaConfig, SUPPORTED_CONFIG_VERSION
from .storage.safety import resolve_data_dir, WriterLease, inspect_database
from .integrations.safety import ReplyPermits, OwnedFrequency, assert_no_old_writer
from .planner.generation_coordinator import ScheduleGenerationCoordinator
from .utils.time_utils import TimeRange
from .services import CleanupService, CommandService, InjectService, ProactiveService, ToolsService


logger = logging.getLogger(__name__)


_REMOVED_SCHEDULE_FIELDS = {
    "sync_sleep_frequency",
    "sleep_talk_adjust",
    "enable_frequency_modulation",
    "day_start_time",
    "day_end_time",
    "llm_task_name",
}


def _find_removed_config_fields(raw: Mapping[str, Any]) -> List[str]:
    """返回 v5.1 明确不再接受的旧配置路径。"""
    removed: List[str] = []
    schedule = raw.get("schedule")
    if isinstance(schedule, Mapping):
        removed.extend(
            f"schedule.{name}" for name in sorted(_REMOVED_SCHEDULE_FIELDS) if name in schedule
        )
    inject = raw.get("inject")
    if isinstance(inject, Mapping) and "inject_mode" in inject:
        removed.append("inject.inject_mode")
    autonomous = raw.get("autonomous_planning")
    if isinstance(autonomous, Mapping) and "schedule" in autonomous:
        removed.append("autonomous_planning.schedule")
    return removed


class MaiVitaPlugin(MaiBotPlugin):
    """麦麦自主规划插件 v5.1（保留类名以维持 Python 导入稳定）。"""

    config_model: ClassVar[type[PluginConfigBase]] = MaiVitaConfig
    config_reload_subscriptions: ClassVar[tuple[str, ...]] = ("bot",)

    def __init__(self) -> None:
        """初始化插件基础字段，service 实例延迟到 ``on_load`` 创建。"""
        super().__init__()
        self._plugin_root: Path = Path(__file__).resolve().parent
        self._tools_svc: ToolsService | None = None
        self._cmd_svc: CommandService | None = None
        self._inject_svc: InjectService | None = None
        self._cleanup_svc: CleanupService | None = None
        self._proactive_svc: ProactiveService | None = None
        self._generation_coordinator = ScheduleGenerationCoordinator()
        self._bg_tasks: List[asyncio.Task] = []
        # Exact one-use receipt tokens; unsupported hosts cannot bypass silence.
        self._reply_permits = ReplyPermits()
        self._data_dir: Path | None = None
        self._writer_lease: WriterLease | None = None
        self._frequency: OwnedFrequency | None = None
        self._loaded = False
        self._owns_manager = False
        # v4 新增：bot 全局配置缓存（on_load 时一次性拉取）
        self._bot_profile: Dict[str, str] = {}

    # ============================================================
    # 配置迁移 Hook（SDK 在校验前调用）
    # ============================================================

    def normalize_plugin_config(self, config_data):
        """Schema 1 only. Convert v5.1 explicitly with tools/convert_config.py."""
        raw = dict(config_data) if isinstance(config_data, Mapping) else {}
        if raw:
            version = str((raw.get("plugin") or {}).get("config_version") or "").strip()
            removed = _find_removed_config_fields(raw)
            if version != SUPPORTED_CONFIG_VERSION or removed:
                raise ValueError(f"不支持直接迁移；config_version={version or '缺失'}；已删除字段: {removed}；使用 tools/convert_config.py")
            MaiVitaConfig.model_validate(raw)  # before SDK merge can discard extras
        return super().normalize_plugin_config(raw)

    async def on_load(self) -> None:
        """插件加载完成：建立数据目录、初始化 services、启动后台任务。"""
        # 仅当插件被启用时才初始化 service 与启动后台任务
        if not self.config.plugin.enabled:
            logger.warning("[v5.1] 插件已禁用（plugin.enabled=False），跳过初始化")
            return

        if self._loaded or self._writer_lease is not None:
            raise RuntimeError("MaiVita already loaded")
        self._generation_coordinator = ScheduleGenerationCoordinator()
        await assert_no_old_writer(self.ctx)
        data_dir = resolve_data_dir(self.ctx, self.config.storage.data_dir)
        if not self.config.storage.writer_lock:
            raise ValueError("configure absolute deployment-wide storage.writer_lock")
        lease = WriterLease(Path(self.config.storage.writer_lock))
        lease.acquire()
        self._writer_lease = lease
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            self._data_dir = data_dir
            db_path = str(data_dir / "goals.db")  # R0 legacy schema, not maivita.db
            if Path(db_path).exists() and not inspect_database(Path(db_path), allow_sidecars=True)["valid_legacy_schema"]:
                raise ValueError("unknown/damaged database; metadata left unchanged")
            from .planner.goal_manager import get_goal_manager
            get_goal_manager(str(data_dir))
            self._owns_manager = True
            from .utils.schedule_image_generator import ScheduleImageGenerator
            ScheduleImageGenerator.configure_output_dir(data_dir / "images")
            self._frequency = OwnedFrequency(self.ctx.frequency)
            if self.config.compatibility.legacy_empty_admin_all:
                logger.warning("legacy_empty_admin_all: all users retain /plan access; review before R2")
            self._bot_profile = await self._prefetch_bot_profile()

            # 初始化 services（依赖注入：plugin 自身 + 数据库路径）
            self._tools_svc = ToolsService(self, db_path=db_path)
            self._cmd_svc = CommandService(self)
            self._inject_svc = InjectService(self)
            self._cleanup_svc = CleanupService(self)
            self._proactive_svc = ProactiveService(self)

            # 启动后台任务（清理循环 + 自动调度循环 + 注入缓存预热 + v4.4 主动行为循环）
            self._bg_tasks.append(asyncio.create_task(self._cleanup_svc.run_cleanup_loop()))
            self._bg_tasks.append(asyncio.create_task(self._cleanup_svc.run_scheduler_loop()))
            self._bg_tasks.append(asyncio.create_task(self._inject_svc.preheat_cache()))
            self._bg_tasks.append(asyncio.create_task(self._proactive_svc.run_loop()))

            self._loaded = True
            logger.info("MaiVita R0 loaded: legacy schedule/sleep, data_dir=%s", data_dir)
        except BaseException:
            await self.on_unload()
            raise

    @property
    def llm_log_dir(self) -> Path:
        if self._data_dir is None:
            raise RuntimeError("data directory not initialized")
        return self._data_dir / "llm_logs"

    async def _prefetch_bot_profile(self) -> Dict[str, str]:
        """预拉取 bot 全局配置（人设、回复风格、兴趣、昵称）。

        失败时返回空字典，PromptBuilder 会使用内置默认值。

        Returns:
            ``{"personality": str, "reply_style": str, "interest": str, "bot_name": str}``
        """
        profile: Dict[str, str] = {}
        try:
            personality = await self.ctx.config.get("personality.personality", "")
            reply_style = await self.ctx.config.get("personality.reply_style", "")
            interest = await self.ctx.config.get("personality.interest", "")
            bot_name = await self.ctx.config.get("bot.nickname", "")
            profile = {
                "personality": str(personality or ""),
                "reply_style": str(reply_style or ""),
                "interest": str(interest or ""),
                "bot_name": str(bot_name or ""),
            }
            logger.debug("[v4] bot_profile 已预拉取: %s", profile)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[v4] 预拉取 bot_profile 失败，使用空值: %s", exc)
        return profile

    # ============================================================
    # 对外暴露：ScheduleGenerator 配置构建（单一来源）
    # ============================================================

    def build_schedule_config(self) -> Dict[str, Any]:
        """构建供 ``ScheduleGenerator`` 使用的 dict 配置。

        统一所有 ScheduleGenerator 构造入口（``ToolsService`` /
        ``ScheduleAutoScheduler`` / ``CommandService``）的配置来源，避免出现
        "部分入口漏带 bot_profile"等路径不一致问题（v4.4.4 修复的 bug 即源于此：
        ``auto_scheduler`` 原本用 ``self.plugin.config.schedule.model_dump()``
        构造 config，而 pydantic 模型里不存在 ``bot_profile`` 字段，导致 prompt
        生成时人设缺失）。

        Returns:
            包含 schedule 段全部强类型字段，以及插件层缓存的 ``bot_profile`` 的
            可变 dict。调用方拿到后可临时覆盖 ``custom_prompt`` 等字段而不影响下次调用。
        """
        cfg = self.config.schedule
        return {
            "use_multi_round": cfg.use_multi_round,
            "max_rounds": cfg.max_rounds,
            "quality_threshold": cfg.quality_threshold,
            "min_activities": cfg.min_activities,
            "max_activities": cfg.max_activities,
            "enable_detailed_description": cfg.enable_detailed_description,
            "min_description_length": cfg.min_description_length,
            "max_description_length": cfg.max_description_length,
            "max_tokens": cfg.max_tokens,
            "generation_timeout": cfg.generation_timeout,
            "generation_max_retries": cfg.generation_max_retries,
            "generation_retry_backoff": cfg.generation_retry_backoff,
            "custom_prompt": cfg.custom_prompt,
            "timezone": cfg.timezone,
            "model_mode": cfg.model.mode,
            "model_task": cfg.model.task,
            "model_name": cfg.model.model,
            "recent_schedule_days": cfg.recent_schedule_days,
            "sleep_start_time": cfg.sleep_start_time,
            "sleep_end_time": cfg.sleep_end_time,
            # v4.5.0：无睡眠模式
            "no_sleep_mode": cfg.no_sleep_mode,
            "bot_profile": dict(self._bot_profile) if self._bot_profile else {},
        }

    async def on_unload(self) -> None:
        """插件卸载：通知所有循环停止 → cancel 后台任务 → 等待退出 → 关闭数据库。"""
        try:
            if self._cleanup_svc is not None:
                await self._cleanup_svc.stop()
            await self._generation_coordinator.stop()
        finally:
            for task in self._bg_tasks:
                task.cancel()
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
            try:
                if self._proactive_svc is not None:
                    await self._proactive_svc.stop()
            finally:
                from .planner.goal_manager import close_goal_manager
                if self._owns_manager:
                    close_goal_manager()
                    self._owns_manager = False
                if self._writer_lease is not None:
                    self._writer_lease.release()
                    self._writer_lease = None
                self._reply_permits.pending.clear()
                self._loaded = False
                self._tools_svc = self._cmd_svc = self._inject_svc = None
                self._cleanup_svc = self._proactive_svc = None

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件。

        Args:
            scope: 变更范围 (``self`` / ``bot`` / ``model``)
            config_data: 当前范围最新配置数据
            version: 配置版本号
        """
        del config_data
        logger.info("[v5.1] 配置热更新 scope=%s version=%s", scope, version)
        if scope == "bot":
            self._bot_profile = await self._prefetch_bot_profile()
            logger.info("[v5.1] bot_profile 已随 bot 配置热更新")
        if scope == "self":
            if getattr(self, "_loaded", False):
                await self.on_unload()
                await self.on_load()
            elif self._proactive_svc is not None:
                await self._proactive_svc.refresh_frequency()
            elif self.config.plugin.enabled:
                await self.on_load()

    def is_sleeping_now(self) -> bool:
        """Return the configured hard sleep state without consulting names."""
        cfg = self.config.schedule
        if cfg.no_sleep_mode:
            return False
        try:
            window = TimeRange.from_strings(cfg.sleep_start_time, cfg.sleep_end_time)
        except ValueError as exc:
            logger.error("睡眠配置非法，运行时不启用静默: %s", exc)
            return False
        now = self._inject_svc._tz_manager.get_now() if self._inject_svc else None
        if now is None:
            from .utils.timezone_manager import TimezoneManager
            now = TimezoneManager(cfg.timezone).get_now()
        return window.contains_minute(now.hour * 60 + now.minute)

    async def send_command_reply(self, stream_id: str, send_operation: Any) -> Any:
        """Request token, never a stream-wide concurrency exception.

        Audited host drops this token before before_send. Such hosts block
        sleeping receipts safely; see R0_REPORT for the integration gap.
        """
        token = self._reply_permits.issue(str(stream_id))
        try:
            return await send_operation(token)
        finally:
            self._reply_permits.pending.pop(token, None)

    def _is_command_reply_send(self, message: Any) -> bool:
        permits = getattr(self, "_reply_permits", None)
        return bool(permits and permits.consume(message))

    @Tool(
        "manage_goal_v4",
        description="管理长期目标，支持创建、查看、更新、暂停、恢复、完成、取消、删除目标",
        parameters=[
            ToolParameterInfo(
                name="action",
                param_type=ToolParamType.STRING,
                description="操作类型: create / list / get / update / pause / resume / complete / cancel / delete",
                required=True,
            ),
            ToolParameterInfo(
                name="goal_id",
                param_type=ToolParamType.STRING,
                description="目标 ID（除 create 和 list 外都需要）",
                required=False,
            ),
            ToolParameterInfo(
                name="name",
                param_type=ToolParamType.STRING,
                description="目标名称（create 时必需）",
                required=False,
            ),
            ToolParameterInfo(
                name="description",
                param_type=ToolParamType.STRING,
                description="目标描述（create 时必需）",
                required=False,
            ),
            ToolParameterInfo(
                name="goal_type",
                param_type=ToolParamType.STRING,
                description="目标类型: health_check / social_maintenance / learn_topic / custom",
                required=False,
            ),
            ToolParameterInfo(
                name="priority",
                param_type=ToolParamType.STRING,
                description="优先级: high / medium / low",
                required=False,
            ),
            ToolParameterInfo(
                name="time_window",
                param_type=ToolParamType.STRING,
                description="时间窗口，格式 'HH:MM-HH:MM'（如 '09:00-10:30'）",
                required=False,
            ),
            ToolParameterInfo(
                name="deadline_hours",
                param_type=ToolParamType.FLOAT,
                description="截止时间（从现在开始的小时数）",
                required=False,
            ),
            ToolParameterInfo(
                name="parameters",
                param_type=ToolParamType.STRING,
                description="目标参数（JSON 字符串）",
                required=False,
            ),
        ],
    )
    async def handle_manage_goal(self, **kwargs: Any) -> Dict[str, Any]:
        """目标管理工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.manage_goal(**kwargs)

    @Tool(
        "get_planning_status_v4",
        description="查看今日日程安排，按时间顺序显示正在进行和即将到来的活动",
        parameters=[
            ToolParameterInfo(
                name="detailed",
                param_type=ToolParamType.BOOLEAN,
                description="是否显示详细信息",
                required=False,
            ),
        ],
    )
    async def handle_get_planning_status(self, **kwargs: Any) -> Dict[str, Any]:
        """规划状态查询工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.get_planning_status(**kwargs)

    @Tool(
        "generate_schedule_v4",
        description="自动生成并应用全局每日/每周/每月计划，使用 LLM 根据 bot 人设智能生成",
        parameters=[
            ToolParameterInfo(
                name="schedule_type",
                param_type=ToolParamType.STRING,
                description="日程类型: daily / weekly / monthly",
                required=True,
            ),
            ToolParameterInfo(
                name="auto_apply",
                param_type=ToolParamType.BOOLEAN,
                description="是否立即应用日程（默认 true）",
                required=False,
            ),
        ],
    )
    async def handle_generate_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """日程生成工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.generate_schedule(**kwargs)

    @Tool(
        "apply_schedule_v4",
        description="应用之前生成的日程，将日程项转换为全局可执行的目标",
        parameters=[
            ToolParameterInfo(
                name="schedule_data",
                param_type=ToolParamType.STRING,
                description="日程数据（从 generate_schedule 获取，JSON 字符串）",
                required=True,
            ),
        ],
    )
    async def handle_apply_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """日程应用工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.apply_schedule(**kwargs)

    @Tool(
        "update_schedule_v4",
        description="根据自然语言请求维护日程：角色裁判决定接受/未来约定/拒绝；接受时调整今日日程，未来约定写入候选清单",
        parameters=[
            ToolParameterInfo(
                name="description",
                param_type=ToolParamType.STRING,
                description="日程变更的自然语言描述（如\"下午两点一起学习\"、\"周末打游戏\"）",
                required=True,
            ),
        ],
    )
    async def handle_update_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """角色裁判式日程更新工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.update_schedule(**kwargs)

    # ============================================================
    # Command 组件
    # ============================================================

    @Command(
        "planning_v4",
        description="日程规划系统管理命令（支持 /plan 与 /规划）",
        pattern=r"(?P<planning_cmd>(?<!\S)/(?:plan|规划)(?:\s.*)?$)",
    )
    async def handle_planning_command(
        self,
        text: str = "",
        stream_id: str = "",
        user_id: str = "",
        platform: str = "",
        group_id: str = "",
        matched_groups: Any = None,
        **kwargs: Any,
    ) -> Tuple[bool, str, bool]:
        """``/plan`` 命令入口，转发给 CommandService。"""
        del kwargs
        if self._cmd_svc is None:
            if stream_id:
                await self.send_command_reply(
                    stream_id,
                    lambda: self.ctx.send.text("插件未启用", stream_id),
                )
            return False, "插件未启用", True
        return await self._cmd_svc.execute(
            text=text,
            stream_id=stream_id,
            user_id=user_id,
            platform=platform,
            group_id=group_id,
            matched_groups=matched_groups if isinstance(matched_groups, dict) else {},
        )

    # ============================================================
    # EventHandler 组件（ON_START 用作启动信号；POST_LLM 不再使用，见 POC_RESULT.md）
    # ============================================================

    @EventHandler(
        "autonomous_planner_v4",
        description="启动事件处理器：用于配合主程序生命周期发出启动日志",
        event_type=EventType.ON_START,
        intercept_message=False,
        weight=10,
    )
    async def handle_on_start(self, **kwargs: Any) -> Tuple[bool, bool, Any, Any, Any]:
        """主程序 ON_START 事件回调。

        所有后台任务已在 ``on_load`` 中启动，这里仅做日志确认。
        """
        del kwargs
        logger.info("[v4] 收到 ON_START 事件，后台任务运行中")
        return True, True, None, None, None

    # ============================================================
    # API 组件（对外暴露，可被其他插件通过 ctx.api.call 调用）
    # ============================================================

    @API(
        "get_current_activity",
        description="返回当前时间段最新的日程活动快照（含描述、时间窗口、即将到来活动）",
        version="1",
        public=True,
    )
    async def api_get_current_activity(
        self,
        chat_id: str = "global",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """对外 API：当前时间段最新的日程活动快照。

        其他插件调用方式::

            snapshot = await self.ctx.api.call(
                "davidblackcn.maibot-autonomous-planning-plugin.get_current_activity",
                chat_id="global",
            )
            if snapshot["has_activity"]:
                print(snapshot["activity"]["name"])  # 例如 "睡前刷手机"

        Args:
            chat_id: 可选的会话 ID 过滤；默认 ``global``（与日程注入一致）。
            **kwargs: 预留扩展，当前未使用。

        Returns:
            dict: 见 ``InjectService.get_current_activity_snapshot`` 的返回结构。
        """
        del kwargs
        if self._inject_svc is None:
            return {
                "has_activity": False,
                "activity": None,
                "next_activities": [],
                "as_of": "",
                "timezone": "",
                "error": "plugin_not_initialized",
            }
        return await self._inject_svc.get_current_activity_snapshot(chat_id or "global")

    @API(
        "davidblackcn.maibot-autonomous-planning-plugin.get_current_activity",
        description="Legacy full-route alias; same single MaiVita engine",
        version="1", public=True,
    )
    async def api_legacy_current_activity(self, chat_id: str = "global", **kwargs: Any) -> Dict[str, Any]:
        # Audited Host resolves a dotted API name by exact entry.name as well as
        # entry.full_name. Tested by executing its real resolver/registry code.
        return await self.api_get_current_activity(chat_id=chat_id, **kwargs)

    # ============================================================
    # HookHandler 组件（v4 注入入口，替代 v3 的 POST_LLM）
    # ============================================================

    @HookHandler(
        "maisaka.planner.before_request",
        name="schedule_inject_v4",
        description="在 Maisaka 向 LLM 发起规划请求前注入当前日程信息",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
    )
    async def handle_inject_schedule(
        self,
        messages: List[Dict[str, Any]] | None = None,
        items: List[Dict[str, Any]] | None = None,
        session_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """日程注入 Hook 入口，转发给 InjectService。"""
        if self._inject_svc is None or not self.config.schedule.inject_schedule:
            return {"action": "continue"}
        key = "items" if items is not None else "messages"
        payload = items if items is not None else messages
        if not isinstance(payload, list):
            logger.warning("unsupported planner payload; injection skipped")
            return {"action": "continue"}
        original = {**kwargs, "session_id": session_id, key: payload}
        if items is not None and messages is not None:
            original["messages"] = messages
        result = await self._inject_svc.inject_into_planner_messages(
            messages=payload, session_id=session_id, **kwargs)
        if "modified_kwargs" in result:
            result["modified_kwargs"] = {**original, key: result["modified_kwargs"]["messages"]}
        return result

    @HookHandler(
        "maisaka.replyer.before_request",
        name="schedule_inject_replyer_v4",
        description="在 Maisaka replyer 调 LLM 前把当前活动注入到 extra_prompt（突出活人感，不要主动提及）",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
    )
    async def handle_inject_replyer(
        self,
        session_id: str = "",
        attempt: int = 1,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """replyer 注入 Hook 入口，转发给 InjectService。"""
        if self._inject_svc is None or not self.config.schedule.inject_into_replyer:
            return {"action": "continue"}
        return await self._inject_svc.inject_into_replyer_extra_prompt(
            session_id=session_id,
            attempt=attempt,
            **kwargs,
        )

    @HookHandler(
        "send_service.before_send",
        name="sleep_silence_guard_v4",
        description="固定睡眠窗口内阻止出站消息；入站消息及其上下文仍正常保留",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def handle_sleep_silence(
        self, message: Any = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """睡眠期阻止模型出站消息，但允许已拦截指令的插件回执。"""
        del kwargs
        if getattr(self, "_loaded", None) is False:
            return {"action": "continue"}
        if self.is_sleeping_now():
            if self._is_command_reply_send(message):
                logger.info("固定睡眠窗口内放行一次插件命令回执")
                return {"action": "continue"}
            logger.info("固定睡眠窗口内阻止一次出站消息；入站上下文未丢弃")
            return {"action": "abort", "reason": "locked_sleep_window"}
        return {"action": "continue"}


AutonomousPlanningPluginV4 = MaiVitaPlugin


def create_plugin() -> MaiVitaPlugin:
    """创建 v4 插件实例（Runner 加载入口）。

    Returns:
        AutonomousPlanningPluginV4: 新的 v4 插件实例
    """
    return MaiVitaPlugin()
