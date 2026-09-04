"""定时任务（cron）调度核心。

设计参考 qwenpaw ``app/crons``：APScheduler ``AsyncIOScheduler`` 驱动、
JSON 文件持久化、每任务执行历史与运行状态。相对 qwenpaw 的简化：

- 没有 channel/多渠道投递 —— 执行结果直接落入会话历史，用户在
  Chat 页即可看到定时任务产生的对话。
- 任务类型只有 ``agent``（向指定 agent 发送一段 prompt）。
- 无人值守执行若触发 HITL 审批，该次运行记为 ``interrupted`` 并把
  审批请求写入会话历史，用户可打开会话完成审批。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, model_validator

from agentcore.repository import _atomic_write_json, _read_json
from agentcore.runtime import paths

logger = logging.getLogger(__name__)

CRON_HISTORY_LIMIT = 50
# 周期性自唤醒：部分平台（如 WSL2）上长延时的 call_later 无法可靠唤醒
# 空闲事件循环，会导致 cron 任务错过触发。短间隔的 keepalive 协程保证
# 事件循环持续巡检到期定时器（与 qwenpaw 同款对策）。
CRON_KEEPALIVE_INTERVAL_SECONDS = 60

# 内部任务（如心跳）的 job id 前缀 —— 不入用户任务列表，不可通过
# /api/cron 端点删除，由所属子系统（如 heartbeat_router）管理生命周期。
INTERNAL_JOB_ID_PREFIX = "_internal:"

JobStatus = Literal[
    "success", "error", "running", "skipped", "interrupted", "cancelled"
]


def _default_timezone() -> str:
    """调度时区默认值：优先本机时区，失败退回 UTC。"""
    try:
        from tzlocal import get_localzone_name

        return get_localzone_name() or "UTC"
    except Exception:  # pylint: disable=broad-except
        return "UTC"


# ---------------------------------------------------------------------------
# Cron 表达式归一化
#
# APScheduler v3 的 CronTrigger(day_of_week=...) 采用 ISO 8601 星期编号
# (0=周一 … 6=周日)，而标准 crontab 是 0=周日 … 6=周六，from_crontab()
# 也不做转换。三字母英文缩写（mon…sun）在两套体系下都无歧义，因此在
# 校验期把第 5 段数字统一转成缩写。
# ---------------------------------------------------------------------------

_CRONTAB_NUM_TO_NAME: dict[str, str] = {
    "0": "sun",
    "1": "mon",
    "2": "tue",
    "3": "wed",
    "4": "thu",
    "5": "fri",
    "6": "sat",
    "7": "sun",
}


def _crontab_dow_to_name(field: str) -> str:
    """把 crontab 星期字段从数字转成缩写（支持 ``*``/列表/区间/步长）。"""
    if field == "*":
        return field

    def _convert_token(tok: str) -> str:
        if "/" in tok:
            base, step = tok.rsplit("/", 1)
            return f"{_convert_token(base)}/{step}"
        if "-" in tok:
            parts = tok.split("-", 1)
            return "-".join(_CRONTAB_NUM_TO_NAME.get(p, p) for p in parts)
        return _CRONTAB_NUM_TO_NAME.get(tok, tok)

    return ",".join(_convert_token(t) for t in field.split(","))


def normalize_cron_5_fields(v: str) -> str:
    """归一化 cron 表达式为 5 段（允许 4/3 段补齐，不支持秒级）。"""
    parts = [p for p in v.split() if p]
    if len(parts) == 5:
        parts[4] = _crontab_dow_to_name(parts[4])
        return " ".join(parts)
    if len(parts) == 4:
        hour, dom, month, dow = parts
        return f"0 {hour} {dom} {month} {_crontab_dow_to_name(dow)}"
    if len(parts) == 3:
        dom, month, dow = parts
        return f"0 0 {dom} {month} {_crontab_dow_to_name(dow)}"
    raise ValueError(
        "cron must have 5 fields (or 4/3 fields that can be normalized); "
        "seconds not supported"
    )


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class ScheduleSpec(BaseModel):
    """调度规格：周期（5 段 cron）或一次性（run_at）。"""

    type: Literal["cron", "once"] = "cron"
    cron: Optional[str] = None
    run_at: Optional[datetime] = None
    timezone: str = Field(default_factory=_default_timezone)

    @model_validator(mode="after")
    def _validate(self) -> "ScheduleSpec":
        if self.type == "cron":
            if not (self.cron and self.cron.strip()):
                raise ValueError("schedule.type is cron but cron is empty")
            self.cron = normalize_cron_5_fields(self.cron)
            self.run_at = None
            return self
        if self.run_at is None:
            raise ValueError("schedule.type is once but run_at is missing")
        self.cron = None
        return self


class JobRuntimeSpec(BaseModel):
    """运行时控制：超时 / 并发 / 错过宽限 / 会话共享。"""

    max_concurrency: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=600, ge=1)
    misfire_grace_seconds: int = Field(default=600, ge=0)
    share_session: bool = Field(
        default=True,
        description=(
            "True: 该任务的所有运行共用一个专属会话 "
            "(``cron:{job_id}``)，历史连续可见；"
            "False: 每次运行创建独立会话，上下文互不干扰。"
        ),
    )


class CronJobSpec(BaseModel):
    """定时任务规格。"""

    id: Optional[str] = None
    name: str
    enabled: bool = True
    agent_id: str = "default"
    message: str = Field(description="发给 agent 的 prompt")
    schedule: ScheduleSpec = Field(default_factory=ScheduleSpec)
    runtime: JobRuntimeSpec = Field(default_factory=JobRuntimeSpec)
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "CronJobSpec":
        if not (self.name and self.name.strip()):
            raise ValueError("name must not be empty")
        if not (self.message and self.message.strip()):
            raise ValueError("message must not be empty")
        if not (self.agent_id and self.agent_id.strip()):
            raise ValueError("agent_id must not be empty")
        return self


class CronJobState(BaseModel):
    """任务运行状态（内存态，随调度器事件更新）。"""

    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[JobStatus] = None
    last_error: Optional[str] = None
    last_session_id: Optional[str] = None


class CronExecutionRecord(BaseModel):
    """单次执行记录（持久化，每任务最多保留 50 条）。"""

    run_at: datetime
    status: JobStatus
    error: Optional[str] = None
    trigger: Literal["scheduled", "manual"] = "scheduled"


class CronJobView(BaseModel):
    """列表/详情接口的返回体：规格 + 状态。"""

    spec: CronJobSpec
    state: CronJobState = Field(default_factory=CronJobState)


class JobsFile(BaseModel):
    version: int = 1
    jobs: list[CronJobSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


class CronStore:
    """``.agentcore/cron_jobs.json`` JSON 文件持久化。

    任务规格与执行历史同文件保存（历史按任务分桶、限长），每次写入
    走原子替换。内存缓存 + 全量刷盘 —— 与 ``ControlPlaneStore`` 同款。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (paths.get_data_dir() / "cron_jobs.json")
        self.jobs: dict[str, CronJobSpec] = {}
        self.history: dict[str, list[CronExecutionRecord]] = {}
        self._load()

    def _load(self) -> None:
        raw = _read_json(self._path)
        if not raw or not isinstance(raw, dict):
            return
        # 逐任务解析 —— 单条损坏的任务不能拖垮其余任务的加载。
        jobs_raw = raw.get("jobs", [])
        if isinstance(jobs_raw, list):
            for item in jobs_raw:
                try:
                    job = CronJobSpec.model_validate(item)
                except Exception:  # pylint: disable=broad-except
                    logger.warning(
                        "Skipping unparsable cron job entry: %r", item
                    )
                    continue
                if job.id:
                    self.jobs[job.id] = job
        history_raw = raw.get("history", {})
        if isinstance(history_raw, dict):
            for job_id, records in history_raw.items():
                if not isinstance(records, list):
                    continue
                parsed: list[CronExecutionRecord] = []
                for rec in records:
                    try:
                        parsed.append(CronExecutionRecord.model_validate(rec))
                    except Exception:  # pylint: disable=broad-except
                        continue
                if parsed:
                    self.history[job_id] = parsed

    def _save(self) -> None:
        data = JobsFile(version=1, jobs=list(self.jobs.values())).model_dump(
            mode="json"
        )
        data["history"] = {
            job_id: [r.model_dump(mode="json") for r in records]
            for job_id, records in self.history.items()
        }
        _atomic_write_json(self._path, data)

    # -- jobs --

    def list_jobs(self) -> list[CronJobSpec]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> CronJobSpec | None:
        return self.jobs.get(job_id)

    def upsert_job(self, spec: CronJobSpec) -> None:
        assert spec.id is not None
        self.jobs[spec.id] = spec
        self._save()

    def delete_job(self, job_id: str) -> bool:
        existed = self.jobs.pop(job_id, None) is not None
        self.history.pop(job_id, None)
        if existed:
            self._save()
        return existed

    # -- history --

    def get_history(self, job_id: str) -> list[CronExecutionRecord]:
        return list(self.history.get(job_id, []))

    def append_history(
        self, job_id: str, record: CronExecutionRecord
    ) -> list[CronExecutionRecord]:
        records = self.history.setdefault(job_id, [])
        records.append(record)
        del records[:-CRON_HISTORY_LIMIT]
        self._save()
        return list(records)


# ---------------------------------------------------------------------------
# 调度管理器
# ---------------------------------------------------------------------------


class CronManager:
    """APScheduler 驱动的定时任务管理器（进程内单例，挂 ``app.state``）。

    执行器直接复用 chat 的图解析与会话持久化逻辑，因此定时任务的
    结果会话会自动出现在 Chat 页的会话列表里。

    除用户任务外还支持 **内部任务**（job id 以 :data:`INTERNAL_JOB_ID_PREFIX`
    开头，如心跳）：它们注册进同一个调度器（享受 misfire 宽限、
    keepalive、执行历史），但回调自带、不入 ``cron_jobs.json``、
    不出现在用户任务列表里。
    """

    def __init__(
        self,
        app_state: Any,
        store: CronStore | None = None,
    ) -> None:
        self._app_state = app_state
        self._store = store or CronStore()
        self._scheduler = AsyncIOScheduler(timezone=_default_timezone())
        self._lock = asyncio.Lock()
        self._states: dict[str, CronJobState] = {}
        self._started = False
        self._keepalive_task: asyncio.Task | None = None
        # 内部任务注册表：job_id -> (callback, trigger, misfire_grace)
        self._internal: dict[str, tuple[Any, Any, int]] = {}

    # ----- lifecycle -----

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self._register_scheduler_listeners()
            self._scheduler.start()
            # 重新注册之前已登记的内部任务（如心跳）—— 调度器重建后
            # APScheduler 侧的注册会丢失，这里按注册表恢复。
            for job_id, (callback, trigger, grace) in list(
                self._internal.items()
            ):
                try:
                    self._register_internal(
                        job_id, callback, trigger, grace
                    )
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "Failed to re-register internal job %s", job_id
                    )
            for job in self._store.list_jobs():
                try:
                    self._register_or_update(job)
                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(
                        "Skipping invalid cron job during startup: "
                        "job_id=%s name=%s error=%s",
                        job.id,
                        job.name,
                        repr(e),
                    )
                    if job.enabled:
                        self._store.upsert_job(
                            job.model_copy(update={"enabled": False})
                        )
            self._started = True
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(), name="cron-keepalive"
            )
            logger.info(
                "CronManager started with %d job(s)", len(self._store.jobs)
            )

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._started = False
            keepalive = self._keepalive_task
            self._keepalive_task = None
            if keepalive is not None:
                keepalive.cancel()
                try:
                    await asyncio.wait_for(keepalive, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:  # pylint: disable=broad-except
                    logger.debug("Error cancelling cron keepalive task")
            self._scheduler.shutdown(wait=False)

    # ----- internal jobs (heartbeat etc.) -----

    def register_internal_job(
        self,
        job_id: str,
        callback: Any,
        trigger: Any,
        *,
        misfire_grace_seconds: int = 600,
    ) -> None:
        """注册/替换一个内部任务（不入用户任务列表、不持久化规格）。

        *callback* 是协程函数，返回运行状态字符串（如 ``"success"`` /
        ``"skipped"``）或 ``{"status": ..., "error": ...}`` dict；
        抛异常记为 ``"error"``。未启动调度器时仅记录注册表，
        :meth:`start` 时再挂进 APScheduler。
        """
        if not job_id.startswith(INTERNAL_JOB_ID_PREFIX):
            job_id = INTERNAL_JOB_ID_PREFIX + job_id
        self._internal[job_id] = (callback, trigger, misfire_grace_seconds)
        if self._started:
            self._register_internal(
                job_id, callback, trigger, misfire_grace_seconds
            )

    def _register_internal(
        self,
        job_id: str,
        callback: Any,
        trigger: Any = None,
        misfire_grace_seconds: int = 600,
    ) -> None:
        """把内部任务挂进 APScheduler（复用现有注册时沿用原触发器）。"""
        if trigger is None:
            existing = self._scheduler.get_job(job_id)
            if existing is None:
                raise ValueError(
                    f"internal job {job_id!r} needs a trigger on first "
                    "registration"
                )
            trigger = existing.trigger
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        self._scheduler.add_job(
            self._internal_callback,
            trigger=trigger,
            id=job_id,
            args=[job_id],
            max_instances=1,
            coalesce=True,
            misfire_grace_time=misfire_grace_seconds,
            replace_existing=True,
        )

    def remove_internal_job(self, job_id: str) -> None:
        """移除内部任务（幂等）。"""
        if not job_id.startswith(INTERNAL_JOB_ID_PREFIX):
            job_id = INTERNAL_JOB_ID_PREFIX + job_id
        self._internal.pop(job_id, None)
        self._states.pop(job_id, None)
        if self._started and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

    def has_internal_job(self, job_id: str) -> bool:
        """内部任务是否已登记（无论调度器是否已启动）。"""
        if not job_id.startswith(INTERNAL_JOB_ID_PREFIX):
            job_id = INTERNAL_JOB_ID_PREFIX + job_id
        return job_id in self._internal

    def get_internal_state(self, job_id: str) -> CronJobState:
        """内部任务的运行状态（上次运行时间 / 状态 / 下次运行时间）。"""
        if not job_id.startswith(INTERNAL_JOB_ID_PREFIX):
            job_id = INTERNAL_JOB_ID_PREFIX + job_id
        state = self._states.get(job_id, CronJobState())
        aps_job = (
            self._scheduler.get_job(job_id) if self._started else None
        )
        if aps_job is not None:
            state = state.model_copy(
                update={"next_run_at": aps_job.next_run_time}
            )
        return state

    async def _internal_callback(self, job_id: str) -> None:
        entry = self._internal.get(job_id)
        if entry is None:
            # 任务在等待执行期间被移除 —— 不记录任何状态。
            return
        callback, trigger, grace = entry
        now = datetime.now().astimezone()
        status: str
        error: str | None = None
        try:
            result = await callback()
            if isinstance(result, dict):
                status = str(result.get("status", "success"))
                error = result.get("error")
            else:
                status = str(result or "success")
        except Exception as exc:  # pylint: disable=broad-except
            status = "error"
            error = repr(exc)
            logger.exception("internal job %s failed", job_id)
        self._internal[job_id] = (callback, trigger, grace)
        self._states[job_id] = CronJobState(
            last_run_at=now,
            last_status=status,  # type: ignore[arg-type]
            last_error=error,
        )
        self._store.append_history(
            job_id,
            CronExecutionRecord(
                run_at=now,
                status=status,  # type: ignore[arg-type]
                error=error,
                trigger="scheduled",
            ),
        )
        logger.info("internal job %s finished: %s", job_id, status)

    async def _keepalive_loop(self) -> None:
        try:
            while self._started:
                await asyncio.sleep(CRON_KEEPALIVE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass

    # ----- read / state -----

    def list_views(self) -> list[CronJobView]:
        """用户任务列表 —— 内部任务（如心跳）不在此列。"""
        return [
            CronJobView(spec=job, state=self.get_state(job.id or ""))
            for job in self._store.list_jobs()
        ]

    def get_job(self, job_id: str) -> CronJobSpec | None:
        return self._store.get_job(job_id)

    def get_state(self, job_id: str) -> CronJobState:
        state = self._states.get(job_id, CronJobState())
        # next_run 以调度器为准（注册/触发后会变化）。
        aps_job = (
            self._scheduler.get_job(job_id) if self._started else None
        )
        if aps_job is not None:
            state = state.model_copy(
                update={"next_run_at": aps_job.next_run_time}
            )
        return state

    def get_history(self, job_id: str) -> list[CronExecutionRecord]:
        return self._store.get_history(job_id)

    # ----- write / control -----

    async def create_or_replace_job(self, spec: CronJobSpec) -> CronJobSpec:
        if spec.id and spec.id.startswith(INTERNAL_JOB_ID_PREFIX):
            raise ValueError(
                f"job ids starting with {INTERNAL_JOB_ID_PREFIX!r} are "
                "reserved for internal jobs"
            )
        async with self._lock:
            self._store.upsert_job(spec)
            if self._started:
                self._register_or_update(spec)
            return spec

    async def delete_job(self, job_id: str) -> bool:
        async with self._lock:
            if self._started and self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)
            self._states.pop(job_id, None)
            return self._store.delete_job(job_id)

    async def delete_jobs_for_agent(self, agent_id: str) -> int:
        """删除所有指向 *agent_id* 的用户定时任务（幂等）。

        在 agent 被删除时调用，防止残留任务在下次触发时通过懒加载
        重建已删除 agent 的工作区（僵尸复活）。返回删除的任务数。
        """
        job_ids = [
            job.id
            for job in self._store.list_jobs()
            if job.agent_id == agent_id and job.id
        ]
        for job_id in job_ids:
            await self.delete_job(job_id)
        return len(job_ids)

    async def pause_job(self, job_id: str) -> None:
        async with self._lock:
            job = self._store.get_job(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            self._store.upsert_job(job.model_copy(update={"enabled": False}))
            if self._scheduler.get_job(job_id):
                self._scheduler.pause_job(job_id)

    async def resume_job(self, job_id: str) -> None:
        async with self._lock:
            job = self._store.get_job(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            self._store.upsert_job(job.model_copy(update={"enabled": True}))
            if self._scheduler.get_job(job_id):
                self._scheduler.resume_job(job_id)

    async def run_job(self, job_id: str) -> None:
        """手动触发一次执行（fire-and-forget，结果只反映在状态/历史）。"""
        job = self._store.get_job(job_id)
        if not job:
            raise KeyError(f"Job not found: {job_id}")
        task = asyncio.create_task(
            self._execute_once(job, trigger="manual"),
            name=f"cron-run-{job_id}",
        )
        task.add_done_callback(self._task_done_cb)

    def _task_done_cb(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "cron background task %s failed: %s",
                task.get_name(),
                repr(exc),
            )

    # ----- scheduler internals -----

    def _register_scheduler_listeners(self) -> None:
        self._scheduler.add_listener(
            self._on_scheduler_event,
            mask=EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES,
        )

    def _on_scheduler_event(
        self, event: JobExecutionEvent | JobSubmissionEvent
    ) -> None:
        if event.code == EVENT_JOB_MISSED:
            job = self._store.get_job(event.job_id)
            if job is not None:
                scheduled = event.scheduled_run_time
                self._record_skipped(
                    job,
                    f"missed scheduled run at {scheduled.isoformat()}",
                )
        elif event.code == EVENT_JOB_MAX_INSTANCES:
            job = self._store.get_job(event.job_id)
            if job is not None:
                self._record_skipped(
                    job,
                    "skipped: maximum running instances reached "
                    f"({job.runtime.max_concurrency})",
                )

    def _record_skipped(self, job: CronJobSpec, error_msg: str) -> None:
        if job.id is None:
            return
        logger.warning(
            "cron job skipped: job_id=%s name=%s %s",
            job.id,
            job.name,
            error_msg,
        )
        st = self._states.get(job.id, CronJobState())
        st.last_status = "skipped"
        st.last_error = error_msg
        st.last_run_at = self._now_in_job_timezone(job)
        self._states[job.id] = st
        self._store.append_history(
            job.id,
            CronExecutionRecord(
                run_at=st.last_run_at,
                status="skipped",
                error=error_msg,
                trigger="scheduled",
            ),
        )

    def _register_or_update(self, spec: CronJobSpec) -> None:
        """把任务注册进调度器（先验证触发器，失败不改动现有状态）。"""
        assert spec.id is not None, "Job must have an id"
        trigger = self._build_trigger(spec)

        if self._scheduler.get_job(spec.id):
            self._scheduler.remove_job(spec.id)
        self._scheduler.add_job(
            self._scheduled_callback,
            trigger=trigger,
            id=spec.id,
            args=[spec.id],
            max_instances=spec.runtime.max_concurrency,
            coalesce=True,
            misfire_grace_time=spec.runtime.misfire_grace_seconds,
            replace_existing=True,
        )
        if not spec.enabled:
            self._scheduler.pause_job(spec.id)

        aps_job = self._scheduler.get_job(spec.id)
        st = self._states.get(spec.id, CronJobState())
        st.next_run_at = aps_job.next_run_time if aps_job else None
        self._states[spec.id] = st

    def _build_trigger(self, spec: CronJobSpec) -> Union[CronTrigger, DateTrigger]:
        if spec.schedule.type == "once":
            assert spec.schedule.run_at is not None
            return DateTrigger(
                run_date=spec.schedule.run_at,
                timezone=spec.schedule.timezone,
            )
        assert spec.schedule.cron is not None
        parts = [p for p in spec.schedule.cron.split() if p]
        if len(parts) != 5:
            raise ValueError(
                f"cron must have 5 fields, got {len(parts)}: {spec.schedule.cron}"
            )
        minute, hour, day, month, day_of_week = parts
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=spec.schedule.timezone,
        )

    @staticmethod
    def _now_in_job_timezone(job: CronJobSpec) -> datetime:
        try:
            tz = ZoneInfo(job.schedule.timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Invalid cron job timezone %r, using UTC (job_id=%s)",
                job.schedule.timezone,
                job.id,
            )
            tz = timezone.utc
        return datetime.now(tz)

    async def _scheduled_callback(self, job_id: str) -> None:
        job = self._store.get_job(job_id)
        if not job:
            return
        try:
            await self._execute_once(job, trigger="scheduled")
        except Exception:  # pylint: disable=broad-except
            logger.exception("cron scheduled run failed: job_id=%s", job_id)
        finally:
            # once 类型触发后调度器自动移除；刷新状态里的下次运行时间。
            aps_job = self._scheduler.get_job(job_id)
            st = self._states.get(job_id, CronJobState())
            st.next_run_at = aps_job.next_run_time if aps_job else None
            self._states[job_id] = st
            # 一次性任务已耗尽 ⇒ 自动停用，避免停留在启用列表里。
            if job.schedule.type == "once" and aps_job is None:
                self._store.upsert_job(
                    job.model_copy(update={"enabled": False})
                )

    # ----- execution -----

    async def _execute_once(
        self,
        job: CronJobSpec,
        *,
        trigger: Literal["scheduled", "manual"] = "scheduled",
    ) -> None:
        assert job.id is not None, "Job must have an id"
        st = self._states.get(job.id, CronJobState())
        st.last_status = "running"
        st.last_error = None
        self._states[job.id] = st

        status: JobStatus
        error: str | None = None
        try:
            result = await asyncio.wait_for(
                _run_agent_once(self._app_state, job),
                timeout=job.runtime.timeout_seconds,
            )
            status = result.get("status", "success")
            error = result.get("error")
            session_id = result.get("session_id")
        except asyncio.TimeoutError:
            status = "error"
            error = f"timed out after {job.runtime.timeout_seconds}s"
            logger.warning(
                "cron execute timed out: job_id=%s timeout=%ss",
                job.id,
                job.runtime.timeout_seconds,
            )
        except asyncio.CancelledError:
            status = "cancelled"
            error = "execution cancelled"
            raise
        except Exception as e:  # pylint: disable=broad-except
            status = "error"
            error = repr(e)
            logger.warning(
                "cron execute failed: job_id=%s error=%s", job.id, repr(e)
            )
        finally:
            st = self._states.get(job.id, CronJobState())
            st.last_run_at = self._now_in_job_timezone(job)
            self._states[job.id] = st

        st.last_status = status
        st.last_error = error
        if session_id:
            st.last_session_id = session_id
        self._states[job.id] = st
        self._store.append_history(
            job.id,
            CronExecutionRecord(
                run_at=st.last_run_at,
                status=status,
                error=error,
                trigger=trigger,
            ),
        )
        logger.info(
            "cron execute finished: job_id=%s trigger=%s status=%s",
            job.id,
            trigger,
            status,
        )


# ---------------------------------------------------------------------------
# 执行器：跑一轮 agent 对话并把结果写入会话历史
# ---------------------------------------------------------------------------


async def _run_agent_once(app_state: Any, job: CronJobSpec) -> dict[str, Any]:
    """以定时任务身份向 *job.agent_id* 发送 *job.message* 并落历史。

    复用 :mod:`chat_router` 的图解析与会话持久化逻辑：

    - ``share_session=True`` ⇒ 专属会话 ``cron:{job_id}``（跨运行累积）
    - ``share_session=False`` ⇒ 每次运行独立会话 ``cron:{job_id}:{run_id}``

    返回 ``{"status": ..., "session_id": ..., "content": ...}``；
    HITL 中断返回 ``status="interrupted"``（审批请求已写入会话）。
    """
    # 延迟导入避免与 chat_router 的模块级初始化纠缠。
    from agentcore.runtime import chat_router
    from agentcore.runtime.agent_ids import known_agent_ids
    from agentcore.runtime.agent_runtime import mark_turn_begin, mark_turn_end
    from agentcore.runtime.chat_router import (
        _append_message,
        _build_approval_request_info,
        _collapse_blank_runs,
        _collect_turn_metadata,
        _ensure_session,
        _extract_interrupts_from_result,
        _extract_text_from_result,
        _extract_usage_from_result,
        _resolve_agent_graph,
    )

    # 僵尸复活防御：目标 agent 已删除时直接跳过 —— 否则懒加载会
    # 把已删除的 workspace 重新建出来。删除 agent 时会同步清理任务，
    # 这里是兜底（如清理前已排入队的一次运行）。
    if job.agent_id not in known_agent_ids(app_state):
        logger.warning(
            "cron run skipped: agent %s no longer exists (job_id=%s)",
            job.agent_id,
            job.id,
        )
        return {
            "status": "skipped",
            "error": f"agent {job.agent_id!r} no longer exists",
        }

    store = getattr(app_state, "store", None)
    manager = getattr(app_state, "agent_manager", None)
    factory = getattr(app_state, "factory", None)
    if store is None or manager is None:
        raise RuntimeError(
            "cron executor requires app.state.store / app.state.agent_manager"
        )

    run_id = uuid.uuid4().hex
    if job.runtime.share_session:
        session_id = f"cron:{job.id}"
    else:
        session_id = f"cron:{job.id}:{run_id}"
    session_id = _ensure_session(store, session_id, job.agent_id)

    _append_message(store, session_id, "user", job.message)

    workspace = await manager.get_or_create_workspace(job.agent_id)
    chat_state = chat_router.get_chat_state(app_state)

    # --- Sandbox session management ---
    # If the agent uses sandbox, ensure the session is active so the cron
    # run executes inside the container (not on the host).
    sandbox_backend = None
    sandbox_mgr = getattr(app_state, "sandbox_session_manager", None)
    if sandbox_mgr is not None:
        try:
            agent_config = workspace.read_agent_config()
            backend_cfg = (agent_config.get("settings") or {}).get("backend", {})
            if backend_cfg.get("type") == "sandbox":
                session = await sandbox_mgr.get_or_create(
                    job.agent_id, backend_cfg,
                )
                if session is not None:
                    sandbox_backend = session.backend
                    logger.info(
                        "Cron %s: sandbox session ready for agent %s "
                        "(id=%s, status=%s)",
                        job.id, job.agent_id,
                        session.sandbox_id, session.status,
                    )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Cron %s: failed to get/create sandbox for agent %s",
                job.id, job.agent_id,
            )

    agent_graph = _resolve_agent_graph(
        job.agent_id,
        workspace,
        factory=factory,
        manager=manager,
        chat_state=chat_state,
        sandbox_backend=sandbox_backend,
    )
    chat_state.threads.setdefault(job.agent_id, set()).add(session_id)

    await mark_turn_begin(app_state, job.agent_id)
    try:
        result = await agent_graph.ainvoke(
            {"messages": [HumanMessage(content=job.message)]},
            config={"configurable": {"thread_id": session_id}},
        )
    finally:
        await mark_turn_end(app_state, job.agent_id)

    # HITL 中断：无人值守不能等待审批 —— 记录审批请求、标记状态，
    # 用户可打开对应会话继续审批流程。
    interrupts = _extract_interrupts_from_result(result)
    if interrupts:
        approval_info = _build_approval_request_info(interrupts)
        _append_message(
            store,
            session_id,
            "assistant",
            "",
            extras={"approval_request": approval_info},
        )
        logger.info(
            "cron run hit HITL interrupt: job_id=%s session=%s",
            job.id,
            session_id,
        )
        return {
            "status": "interrupted",
            "session_id": session_id,
            "error": "pending approval",
        }

    content, tool_calls = _extract_text_from_result(result)
    content = _collapse_blank_runs(content)

    # Token 统计（尽力而为，与 chat 同步端点一致）。
    input_tokens, output_tokens = _extract_usage_from_result(result)
    if input_tokens or output_tokens:
        try:
            from agentcore.runtime.model_factory import build_model_string
            from agentcore.runtime.token_router import record_token_usage

            agent_config = workspace.read_agent_config()
            model_str = build_model_string(agent_config.get("model")) or ""
            record_token_usage(
                job.agent_id, input_tokens, output_tokens, model_str
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "cron: failed to record token usage for job %s",
                job.id,
                exc_info=True,
            )

    extras = _collect_turn_metadata(result)
    extras = dict(extras or {})
    extras["cron_job_id"] = job.id
    _append_message(
        store,
        session_id,
        "assistant",
        content,
        tool_calls or None,
        extras=extras,
    )

    # Best-effort pull-back: files the agent created inside the sandbox
    # are mirrored back to the workspace so they appear in the Files page.
    if sandbox_backend is not None:
        try:
            await chat_router._pull_sandbox_storage(
                app_state, job.agent_id, workspace,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug(
                "Cron %s: sandbox pull-back failed (non-fatal)", job.id,
            )

    return {"status": "success", "session_id": session_id, "content": content}
