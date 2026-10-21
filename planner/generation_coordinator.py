"""全局日程生成协调器。

所有会触发完整日程生成链的入口共享同一个实例，避免命令重试、自动调度
和 Tool 调用并发触发重复的上游 LLM 请求。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar
import asyncio
import logging
import uuid


logger = logging.getLogger(__name__)
T = TypeVar("T")
GenerationOperation = Callable[[str], Awaitable[T]]
CompletionCallback = Callable[[Optional[T], Optional[BaseException]], Awaitable[None]]


class GenerationInProgressError(RuntimeError):
    """已有全局日程生成主链正在运行。"""

    def __init__(self, generation_id: str) -> None:
        super().__init__(f"当前已有日程生成任务正在进行，请稍候（generation_id={generation_id}）")
        self.generation_id = generation_id


@dataclass
class GenerationStatus:
    state: str = "idle"
    generation_id: str = ""
    source: str = ""
    started_at: str = ""
    finished_at: str = ""
    current_round: int = 0
    current_retry: int = 0
    error: str = ""


class ScheduleGenerationCoordinator(Generic[T]):
    """串行化完整生成主链，并保存可查询的运行状态。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current_task: Optional[asyncio.Task[Any]] = None
        self._status = GenerationStatus()

    @staticmethod
    def new_generation_id() -> str:
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{now}-{uuid.uuid4().hex[:6]}"

    @property
    def is_running(self) -> bool:
        task = self._current_task
        return bool(task is not None and not task.done())

    def get_status(self) -> dict[str, Any]:
        data = asdict(self._status)
        if self.is_running and self._status.started_at:
            try:
                started = datetime.fromisoformat(self._status.started_at)
                data["elapsed_seconds"] = max(
                    0.0, (datetime.now(timezone.utc) - started).total_seconds()
                )
            except ValueError:
                data["elapsed_seconds"] = 0.0
        else:
            data["elapsed_seconds"] = 0.0
        return data

    def update_progress(self, generation_id: str, *, round_num: int, retry: int) -> None:
        if self._status.generation_id != generation_id or not self.is_running:
            return
        self._status.current_round = round_num
        self._status.current_retry = retry

    async def run_exclusive(self, operation: GenerationOperation[T], *, source: str) -> T:
        """在调用方任务中运行一条主链；已有任务时立即拒绝而不是排队。"""
        if self.is_running:
            raise GenerationInProgressError(self._status.generation_id)

        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("日程生成必须运行在 asyncio Task 中")
        generation_id = self.new_generation_id()
        self._current_task = current
        return await self._execute(operation, source, generation_id, current)

    def start_background(
        self,
        operation: GenerationOperation[T],
        *,
        source: str,
        on_complete: Optional[CompletionCallback[T]] = None,
    ) -> tuple[bool, str]:
        """启动后台主链，方法本身不等待 LLM。"""
        if self.is_running:
            return False, self._status.generation_id

        generation_id = self.new_generation_id()
        # 先登记状态再调度 task，确保紧随其后的第二个命令能拿到同一个 ID。
        self._status = GenerationStatus(
            state="running",
            generation_id=generation_id,
            source=source,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        async def runner() -> None:
            result: Optional[T] = None
            error: Optional[BaseException] = None
            try:
                result = await self._execute(
                    operation, source, generation_id, asyncio.current_task()
                )
            except asyncio.CancelledError as exc:
                error = exc
                raise
            except BaseException as exc:  # 后台异常必须集中记录，不能静默吞掉
                error = exc
            finally:
                if on_complete is not None:
                    try:
                        await on_complete(result, error)
                    except Exception:
                        logger.exception(
                            "generation_id=%s 后台生成完成通知失败", generation_id
                        )

        task = asyncio.create_task(runner(), name=f"schedule-generation-{generation_id}")
        # 在 create_task 后立即登记；同一事件循环内没有 await，因此并发入口无法插队。
        self._current_task = task
        return True, generation_id

    async def _execute(
        self,
        operation: GenerationOperation[T],
        source: str,
        generation_id: str,
        owner_task: Optional[asyncio.Task[Any]],
    ) -> T:
        started_at = datetime.now(timezone.utc)
        self._status = GenerationStatus(
            state="running",
            generation_id=generation_id,
            source=source,
            started_at=started_at.isoformat(),
        )
        try:
            async with self._lock:
                logger.info(
                    "generation_id=%s source=%s schedule generation started",
                    generation_id,
                    source,
                )
                result = await operation(generation_id)
            self._status.state = "success"
            logger.info(
                "generation_id=%s source=%s schedule generation succeeded elapsed=%.3fs",
                generation_id,
                source,
                (datetime.now(timezone.utc) - started_at).total_seconds(),
            )
            return result
        except asyncio.CancelledError:
            self._status.state = "cancelled"
            self._status.error = "CancelledError"
            logger.warning(
                "generation_id=%s generation cancelled; 当前 Provider 请求可能仍在上游执行，"
                "但本 generation 已取消，不再继续后续轮次/retry/apply",
                generation_id,
            )
            raise
        except BaseException as exc:
            self._status.state = "failed"
            self._status.error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "generation_id=%s source=%s schedule generation failed: %s",
                generation_id,
                source,
                exc,
            )
            raise
        finally:
            self._status.finished_at = datetime.now(timezone.utc).isoformat()
            if self._current_task is owner_task:
                self._current_task = None

    async def stop(self) -> None:
        """取消仍在运行的主链并等待其完成清理。"""
        task = self._current_task
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
