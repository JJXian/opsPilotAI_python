"""对话接口

提供基于 RAG Agent 的普通对话和流式对话接口
"""

import json

from fastapi import APIRouter, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.agent.mcp_client import format_exception_chain
from app.models.request import ChatRequest, ClearRequest
from app.models.response import ApiResponse, SessionInfoResponse
from app.services.agentic_rag_service import agentic_rag_service
from app.services.aiops_service import aiops_service
from app.services.bugfix_service import bugfix_service
from app.services.conversation_service import conversation_service
from app.services.rag_agent_service import rag_agent_service

router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    """快速对话接口
    {
        "code": 200,
        "message": "success",
        "data": {
            "success": true,
            "answer": "回答内容",
            "errorMessage": null
        }
    }

    Args:
        request: 对话请求

    Returns:
        统一格式的对话响应
    """
    try:
        logger.info(f"[会话 {request.id}] 收到快速对话请求: {request.question}")
        await conversation_service.add_message(request.id, "user", request.question)
        result = await agentic_rag_service.query(
            request.question,
            session_id=request.id,
            force_rag=request.force_rag,
        )
        answer = result["answer"]
        await conversation_service.add_message(
            request.id,
            "assistant",
            answer,
            tool_payload={"agentic_trace": result.get("trace", [])},
        )
        await conversation_service.maybe_refresh_summary(request.id, agentic_rag_service.model)

        logger.info(f"[会话 {request.id}] 快速对话完成")

        return {
            "code": 200,
            "message": "success",
            "data": {
                "success": True,
                "answer": answer,
                "trace": result.get("trace", []),
                "errorMessage": None,
            },
        }

    except Exception as e:
        logger.error(f"对话接口错误: {e}")
        return {
            "code": 500,
            "message": "error",
            "data": {"success": False, "answer": None, "errorMessage": str(e)},
        }


@router.post("/chat_stream")
async def chat_stream(request: ChatRequest):
    """流式对话接口（基于 RAG Agent，SSE）

    返回 SSE 格式，data 字段为 JSON：

    工具调用事件:
    event: message
    data: {"type":"tool_call","data":{"tool":"工具名","status":"start|end","input":{...}}}

    内容流式事件:
    event: message
    data: {"type":"content","data":"内容块"}

    完成事件:
    event: message
    data: {"type":"done","data":{"answer":"完整答案","tool_calls":[...]}}

    Args:
        request: 对话请求

    Returns:
        SSE 事件流
    """
    logger.info(f"[会话 {request.id}] 收到流式对话请求: {request.question}")

    async def event_generator():
        full_answer = ""
        try:
            await conversation_service.add_message(request.id, "user", request.question)
            async for chunk in agentic_rag_service.run(
                request.question,
                session_id=request.id,
                force_rag=request.force_rag,
            ):
                chunk_type = chunk.get("type", "unknown")
                chunk_data = chunk.get("data", None)

                # 处理调试类型消息（新增）
                if chunk_type == "trace":
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "trace", "data": chunk_data}, ensure_ascii=False
                        ),
                    }
                elif chunk_type == "debug":
                    # 调试信息，可以选择发送或忽略
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "type": "debug",
                                "node": chunk.get("node", "unknown"),
                                "message_type": chunk.get("message_type", "unknown"),
                            },
                            ensure_ascii=False,
                        ),
                    }
                elif chunk_type == "tool_call":
                    # 发送工具调用事件（可选，前端可以显示工具调用状态）
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "tool_call", "data": chunk_data}, ensure_ascii=False
                        ),
                    }
                elif chunk_type == "search_results":
                    # 发送检索结果（可选，前端可以忽略）
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "search_results", "data": chunk_data}, ensure_ascii=False
                        ),
                    }
                elif chunk_type == "content":
                    full_answer += str(chunk_data or "")
                    # 发送内容块 - 关键：data 必须是 JSON 字符串
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "content", "data": chunk_data}, ensure_ascii=False
                        ),
                    }
                elif chunk_type == "complete":
                    completion = chunk_data or {}
                    full_answer = str(completion.get("answer", full_answer))
                    await conversation_service.add_message(
                        request.id,
                        "assistant",
                        full_answer,
                        tool_payload={"agentic_trace": completion.get("trace", [])},
                    )
                    await conversation_service.maybe_refresh_summary(
                        request.id, agentic_rag_service.model
                    )
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "content", "data": full_answer}, ensure_ascii=False
                        ),
                    }
                    # 发送完成信号
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "done", "data": chunk_data}, ensure_ascii=False
                        ),
                    }
                elif chunk_type == "error":
                    await conversation_service.add_message(
                        request.id,
                        "assistant",
                        str(chunk_data),
                        status="failed",
                    )
                    # 发送错误信息
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "error", "data": str(chunk_data)}, ensure_ascii=False
                        ),
                    }

            logger.info(f"[会话 {request.id}] 流式对话完成")

        except Exception as e:
            logger.error(f"流式对话接口错误: {format_exception_chain(e)}")
            yield {
                "event": "message",
                "data": json.dumps({"type": "error", "data": str(e)}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


@router.post("/chat/clear", response_model=ApiResponse)
async def clear_session(request: ClearRequest):
    """清空会话历史

    Args:
        request: 清空请求

    Returns:
        操作结果
    """
    try:
        success = await conversation_service.clear_session(request.session_id)
        await rag_agent_service.clear_checkpoint(request.session_id)
        await agentic_rag_service.clear_checkpoint(request.session_id)
        await aiops_service.clear_checkpoint(request.session_id)
        await bugfix_service.clear_checkpoint(request.session_id)
        logger.info(f"清空会话: {request.session_id}, 结果: {success}")

        return ApiResponse(
            status="success" if success else "error",
            message="会话已清空" if success else "清空会话失败",
            data=None,
        )

    except Exception as e:
        logger.error(f"清空会话错误: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/chat/session/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str) -> SessionInfoResponse:
    """查询会话历史

    Args:
        session_id: 会话 ID

    Returns:
        会话信息
    """
    try:
        history = await conversation_service.get_messages(session_id)
        normalized_history = [
            {
                "role": item["role"],
                "content": item["content"],
                "timestamp": item["created_at"].isoformat(),
            }
            for item in history
            if item["role"] in {"user", "assistant"}
        ]

        return SessionInfoResponse(
            session_id=session_id,
            message_count=len(normalized_history),
            history=normalized_history,
        )

    except Exception as e:
        logger.error(f"获取会话信息错误: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/chat/sessions")
async def list_sessions(page: int = 1, page_size: int = 20):
    """分页获取持久化会话列表。"""
    try:
        return await conversation_service.list_sessions(page, page_size)
    except Exception as e:
        logger.error(f"获取会话列表错误: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/chat/session/{session_id}", response_model=ApiResponse)
async def delete_session(session_id: str) -> ApiResponse:
    """删除会话消息、摘要和两个 Agent 的 Checkpoint。"""
    try:
        deleted = await conversation_service.clear_session(session_id)
        await rag_agent_service.clear_checkpoint(session_id)
        await aiops_service.clear_checkpoint(session_id)
        return ApiResponse(
            status="success" if deleted else "error",
            message="会话已删除" if deleted else "会话不存在",
        )
    except Exception as e:
        logger.error(f"删除会话错误: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
