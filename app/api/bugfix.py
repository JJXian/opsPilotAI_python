"""日志驱动的 Bug 修复建议接口。"""

from __future__ import annotations

import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.models.bugfix import BugFixRequest
from app.services.bugfix_service import bugfix_service
from app.services.conversation_service import conversation_service

router = APIRouter()


@router.post("/bugfix")
async def diagnose_bug(request: BugFixRequest) -> EventSourceResponse:
    """根据 Python 异常堆栈生成只读的定位与修复建议。"""

    async def event_generator():
        final_report = ""
        await conversation_service.add_message(
            request.session_id,
            "user",
            f"提交 Bug 修复分析：\n{request.log}",
            agent_type="bugfix",
        )
        async for event in bugfix_service.run(request.log, request.session_id, request.include_diff):
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
