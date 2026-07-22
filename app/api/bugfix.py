"""日志驱动的 Bug 修复建议接口。"""

from __future__ import annotations

import json

from fastapi import APIRouter
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


@router.post("/bugfix")
async def diagnose_bug(request: BugFixRequest) -> EventSourceResponse:
    """根据 Python 异常堆栈生成只读的定位与修复建议。"""

    async def event_generator():
        final_report = ""
        log = request.log or ""
        if request.log_source_id:
            try:
                fetched = server_log_service.fetch(request.log_source_id)
            except (ValueError, RuntimeError) as error:
                yield {"event": "message", "data": json.dumps({"type": "error", "message": str(error)}, ensure_ascii=False)}
                return
            log = fetched["log"]
            yield {
                "event": "message",
                "data": json.dumps(
                    {"type": "trace", "stage": "fetch_logs", "data": {"message": f"已从日志源「{fetched['source_name']}」读取最近日志"}},
                    ensure_ascii=False,
                ),
            }
        await conversation_service.add_message(
            request.session_id,
            "user",
            f"提交 Bug 修复分析：\n{log}",
            agent_type="bugfix",
        )
        async for event in bugfix_service.run(log, request.session_id, request.include_diff):
            if event["type"] == "report":
                final_report = str(event["report"])
            if event["type"] == "complete":
                final_report = final_report or str(event.get("response", ""))
                await conversation_service.add_message(
                    request.session_id,
                    "assistant",
                    final_report,
                    tool_payload={"bugfix_trace": event.get("trace", [])},
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
