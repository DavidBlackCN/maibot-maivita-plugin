"""Sleep-aware frequency synchronization and proactive activity messages."""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple
import asyncio
import logging
import time

from ..planner.goal_manager import get_goal_manager
from ..core.activity_state import public_activity_text
from ..core.frequency import calculate_frequency
from ...shared.utils.time_utils import parse_time_window
from ...shared.utils.timezone_manager import TimezoneManager

if TYPE_CHECKING:
    from ..plugin import AutonomousPlanningPluginV4

logger = logging.getLogger(__name__)

class ProactiveService:
    """Poll current activity while keeping sleep policy in one shared state source."""

    PROACTIVE_FRESH_WINDOW_MINUTES = 5
    LOOP_INTERVAL_SECONDS = 60.0
    STARTUP_DELAY_SECONDS = 15.0

    def __init__(self, plugin: "AutonomousPlanningPluginV4") -> None:
        self._plugin = plugin
        self._stop_event = asyncio.Event()
        self._tz_manager = TimezoneManager(plugin.config.schedule.timezone)
        self._proactive_history: Dict[Tuple[str, str, str], float] = {}
        self._current_factor: Dict[str, float] = {}

    async def run_loop(self) -> None:
        logger.info("活动驱动与睡眠频率同步循环已启动")
        try:
            await asyncio.sleep(self.STARTUP_DELAY_SECONDS)
            while not self._stop_event.is_set():
                try:
                    await self._check_and_act()
                except Exception as exc:  # noqa: BLE001
                    logger.error("主动行为检查异常: %s", exc, exc_info=True)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), self.LOOP_INTERVAL_SECONDS)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            logger.info("活动驱动与睡眠频率同步循环已退出")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._current_factor:
            await self._set_adjust(list(self._current_factor), 1.0, "stopped")

    async def refresh_frequency(self) -> None:
        """配置热更新后立即重算，避免等待下一次 60 秒轮询。"""
        self._tz_manager = TimezoneManager(self._plugin.config.schedule.timezone)
        await self._sync_frequency()

    async def _check_and_act(self) -> None:
        cfg = self._plugin.config.schedule
        proactive_streams = await self._resolve_proactive_streams(list(cfg.proactive_streams or []))
        sleeping = self._plugin.is_sleeping_now()
        activity = await self._find_current_activity()
        await self._sync_frequency(sleeping=sleeping, activity=activity)

        if sleeping:
            logger.debug("当前状态为 SLEEPING：跳过插件主动消息与提醒")
            return
        if not proactive_streams:
            return

        if activity is None:
            return
        name, goal_type, start, _end, params = activity
        if not cfg.enable_proactive_trigger:
            return

        now = self._tz_manager.get_now()
        current = now.hour * 60 + now.minute
        elapsed = (current - start) % 1440
        if elapsed > self.PROACTIVE_FRESH_WINDOW_MINUTES:
            return
        date_key = now.strftime("%Y-%m-%d")
        for stream_id in proactive_streams:
            key = (stream_id, name, date_key)
            if key in self._proactive_history:
                continue
            await self._trigger_proactive(stream_id, name, goal_type, params)
            self._proactive_history[key] = time.time()

    async def _get_frequency_streams(self, configured: list[str]) -> list[str]:
        if configured:
            return await self._resolve_proactive_streams(configured)
        result: list[str] = []
        try:
            streams = await self._plugin.ctx.chat.get_all_streams()
            for stream in streams or []:
                if isinstance(stream, dict):
                    stream_id = str(stream.get("session_id") or stream.get("stream_id") or "").strip()
                    if stream_id and stream_id not in result:
                        result.append(stream_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取聊天流失败，暂时无法同步动态频率: %s", exc)
        return result

    async def _resolve_proactive_streams(self, entries: list[str]) -> list[str]:
        resolved: list[str] = []
        for raw in entries:
            entry = str(raw).strip()
            if not entry:
                continue
            stream_id: Optional[str]
            if entry.startswith("session:"):
                stream_id = entry.removeprefix("session:").strip() or None
            elif ":group:" in entry or ":private:" in entry:
                platform, kind, target = entry.split(":", 2)
                try:
                    if kind == "group":
                        stream = await self._plugin.ctx.chat.get_stream_by_group_id(target, platform)
                    else:
                        stream = await self._plugin.ctx.chat.get_stream_by_user_id(target, platform)
                    stream_id = str((stream or {}).get("session_id") or (stream or {}).get("stream_id") or "") or None
                except Exception as exc:  # noqa: BLE001
                    logger.warning("解析主动行为流 %s 失败: %s", entry, exc)
                    stream_id = None
            else:
                stream_id = entry
            if stream_id and stream_id not in resolved:
                resolved.append(stream_id)
        return resolved

    async def _find_current_activity(self) -> Optional[Tuple[str, str, int, int, Dict[str, Any]]]:
        now = self._tz_manager.get_now()
        current = now.hour * 60 + now.minute
        for goal in get_goal_manager().get_active_goals(chat_id="global"):
            if goal.goal_type == "pending_commitment":
                continue
            params = dict(goal.parameters or {})
            raw_range = params.get("time_window") or (goal.conditions or {}).get("time_window")
            start, end = parse_time_window(raw_range)
            if start is None or end is None:
                continue
            comparison = current + 1440 if end > 1440 and current < end - 1440 else current
            if start <= comparison < end:
                public_name, _ = public_activity_text(
                    goal.name, goal.description, params
                )
                return public_name, goal.goal_type or "daily_routine", start, end, params
        return None

    async def _sync_frequency(
        self,
        *,
        sleeping: Optional[bool] = None,
        activity: Optional[Tuple[str, str, int, int, Dict[str, Any]]] = None,
    ) -> None:
        cfg = self._plugin.config.schedule
        frequency_cfg = cfg.frequency
        streams = await self._get_frequency_streams(list(frequency_cfg.frequency_streams or []))
        stale_streams = [stream for stream in self._current_factor if stream not in streams]
        if stale_streams:
            await self._set_adjust(stale_streams, 1.0, "removed")
        if not streams:
            return
        if sleeping is None:
            sleeping = self._plugin.is_sleeping_now()
        if activity is None and not sleeping:
            activity = await self._find_current_activity()
        goal_type = activity[1] if activity is not None else ""
        breakdown = calculate_frequency(
            base_frequency=1.0,
            goal_type=goal_type,
            sleeping=sleeping,
            no_sleep_mode=cfg.no_sleep_mode,
            frequency_config=frequency_cfg,
        )
        await self._set_adjust(
            streams,
            breakdown.adjust,
            "sleeping" if sleeping else (goal_type or "idle"),
        )

    async def _set_adjust(self, streams: list[str], target: float, state: str) -> None:
        for stream_id in streams:
            if self._current_factor.get(stream_id) == target and state not in {"removed", "stopped"}:
                continue
            try:
                frequency = self._plugin._frequency
                if target == 1.0 and state in {"removed", "stopped"}:
                    success = await frequency.remove_adjust(stream_id)
                else:
                    success = await frequency.set_adjust(stream_id, target)
                if success is False or (isinstance(success, dict) and not success.get("success", False)):
                    logger.warning("频率调整失败: stream=%s factor=%s", stream_id, target)
                    continue
                if target == 1.0 and state in {"removed", "stopped"}:
                    self._current_factor.pop(stream_id, None)
                else:
                    self._current_factor[stream_id] = target
                logger.info("频率乘数已更新: stream=%s state=%s factor=%s", stream_id, state, target)
            except Exception as exc:  # noqa: BLE001
                logger.warning("频率调整异常: stream=%s: %s", stream_id, exc)

    async def _trigger_proactive(
        self, stream_id: str, activity_name: str, goal_type: str, params: Dict[str, Any]
    ) -> None:
        if self._plugin.is_sleeping_now():
            return
        is_commitment = bool(params.get("is_commitment"))
        intent = (
            f"此前已确认的约定到期：{params.get('commitment_title') or activity_name}。请自然提醒对方。"
            if is_commitment else f"Bot 刚开始{activity_name}，可结合当前对话自然说一句近况。"
        )
        try:
            await self._plugin.ctx.maisaka.proactive.trigger(
                stream_id=stream_id,
                intent=intent,
                reason=f"maibot-maivita-plugin:{activity_name}",
                metadata={
                    "source": "maibot-maivita-plugin",
                    "priority": "high" if is_commitment else "normal",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("主动发起异常: stream=%s: %s", stream_id, exc)
