"""定时任务管理 API（``/api/cron/*``）。

薄路由层：所有逻辑委托给挂在 ``app.state.cron_manager`` 上的
:class:`~agentcore.runtime.cron_manager.CronManager`。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from agentcore.runtime.cron_manager import (
    CronExecutionRecord,
    CronJobSpec,
    CronJobView,
    CronManager,
    CronJobState,
)

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _get_manager(request: Request) -> CronManager:
    manager = getattr(request.app.state, "cron_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="CronManager not ready")
    return manager


@router.get("/jobs", response_model=list[CronJobView])
async def list_jobs(request: Request) -> list[CronJobView]:
    return _get_manager(request).list_views()


@router.get("/jobs/{job_id}", response_model=CronJobView)
async def get_job(job_id: str, request: Request) -> CronJobView:
    mgr = _get_manager(request)
    job = mgr.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return CronJobView(spec=job, state=mgr.get_state(job_id))


@router.post("/jobs", response_model=CronJobSpec)
async def create_job(spec: CronJobSpec, request: Request) -> CronJobSpec:
    """创建任务 —— id 由服务端生成，忽略客户端传入的 spec.id。"""
    mgr = _get_manager(request)
    created = spec.model_copy(update={"id": uuid.uuid4().hex})
    try:
        return await mgr.create_or_replace_job(created)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.put("/jobs/{job_id}", response_model=CronJobSpec)
async def replace_job(
    job_id: str, spec: CronJobSpec, request: Request
) -> CronJobSpec:
    mgr = _get_manager(request)
    if mgr.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if spec.id is not None and spec.id != job_id:
        raise HTTPException(status_code=400, detail="job_id mismatch")
    spec = spec.model_copy(update={"id": job_id})
    try:
        return await mgr.create_or_replace_job(spec)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, request: Request) -> dict[str, bool]:
    ok = await _get_manager(request).delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found")
    return {"deleted": True}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, request: Request) -> dict[str, bool]:
    try:
        await _get_manager(request).pause_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="job not found") from e
    return {"paused": True}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, request: Request) -> dict[str, bool]:
    try:
        await _get_manager(request).resume_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="job not found") from e
    return {"resumed": True}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str, request: Request) -> dict[str, bool]:
    """手动触发一次执行 —— 立即返回，结果体现在状态与执行历史上。"""
    try:
        await _get_manager(request).run_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="job not found") from e
    return {"started": True}


@router.get("/jobs/{job_id}/state", response_model=CronJobState)
async def get_job_state(job_id: str, request: Request) -> CronJobState:
    mgr = _get_manager(request)
    if mgr.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    return mgr.get_state(job_id)


@router.get("/jobs/{job_id}/history", response_model=list[CronExecutionRecord])
async def get_job_history(
    job_id: str, request: Request
) -> list[CronExecutionRecord]:
    mgr = _get_manager(request)
    if mgr.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    return mgr.get_history(job_id)
