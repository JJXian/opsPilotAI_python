"""日志驱动的 Bug 修复建议接口。"""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.bugfix import BugFixRequest
from app.services.bugfix_service import bugfix_service
from app.services.conversation_service import conversation_service
from app.services.server_log_service import server_log_service

router = APIRouter()


@router.get("/bugfix/log-sources")
async def list_log_sources() -> dict[str, list[dict[str, str]]]:
    """返回可选日志源的公开名称；不会返回服务器地址或路径。"""
    return {"sources": server_log_service.available_sources()}


@router.get("/bugfix/incidents/{incident_id}")
async def get_incident(incident_id: str) -> dict:
    """查看可恢复 Incident 的当前计划、预算和审批状态。"""
    incident = await bugfix_service.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident 不存在")
    return incident


@router.post("/bugfix")
async def diagnose_bug(request: BugFixRequest) -> EventSourceResponse:
    """启动动态诊断，或用审批结果恢复同一个 Incident。"""

    async def event_generator():
        final_report = ""
        incident_id = request.incident_id or f"incident-{uuid4()}"
        if request.approval is not None:
            event_stream = bugfix_service.resume(
                request.session_id,
                incident_id,
                request.approval,
            )
            async for event in event_stream:
                if event["type"] == "report":
                    final_report = str(event["report"])
                if event["type"] == "complete":
                    final_report = final_report or str(event.get("response", ""))
                    await conversation_service.add_message(
                        request.session_id,
                        "assistant",
                        final_report,
                        tool_payload={
                            "incident_id": incident_id,
                            "bugfix_trace": event.get("trace", []),
                            "termination_reason": event.get("termination_reason", ""),
                        },
                        agent_type="bugfix",
                    )
                yield {"event": "message", "data": json.dumps(event, ensure_ascii=False)}
            return

        log = request.log or ""
        if request.log_source_id:
            try:
                fetched = server_log_service.fetch(request.log_source_id)
            except (ValueError, RuntimeError) as error:
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {"type": "error", "message": str(error)}, ensure_ascii=False
                    ),
                }
                return
            log = fetched["log"]
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "trace",
                        "stage": "fetch_logs",
                        "data": {"message": f"已从日志源「{fetched['source_name']}」读取最近日志"},
                    },
                    ensure_ascii=False,
                ),
            }
        await conversation_service.add_message(
            request.session_id,
            "user",
            f"提交 Bug 修复分析：\n{log}",
            agent_type="bugfix",
        )
        async for event in bugfix_service.run(
            log,
            request.session_id,
            request.include_diff,
            incident_id,
        ):
            if event["type"] == "report":
                final_report = str(event["report"])
            if event["type"] == "complete":
                final_report = final_report or str(event.get("response", ""))
                await conversation_service.add_message(
                    request.session_id,
                    "assistant",
                    final_report,
                    tool_payload={
                        "incident_id": incident_id,
                        "bugfix_trace": event.get("trace", []),
                        "termination_reason": event.get("termination_reason", ""),
                    },
                    agent_type="bugfix",
                )
            if event["type"] == "error":
                await conversation_service.add_message(
                    request.session_id,
                    "assistant",
                    str(event.get("message", "Bug 修复分析失败")),
                    status="failed",
                    agent_type="bugfix",
                )
            yield {"event": "message", "data": json.dumps(event, ensure_ascii=False)}

    return EventSourceResponse(event_generator())
