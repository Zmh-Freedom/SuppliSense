import asyncio
import uuid

from pydantic import BaseModel

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.agent import chat as agent_chat
from app.services.agent import chat_stream as agent_chat_stream
from app.services.agent import chat_stream_with_plan as agent_chat_stream_with_plan

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    mode: str = "react"  # "react" or "plan-execute"


@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    reply = await asyncio.to_thread(agent_chat, sid, req.message)
    return {"reply": reply, "session_id": sid}


@router.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    """Streaming chat endpoint using SSE (Server-Sent Events)."""
    sid = req.session_id or str(uuid.uuid4())

    # Choose execution mode
    if req.mode == "plan-execute":
        stream_fn = agent_chat_stream_with_plan
    else:
        stream_fn = agent_chat_stream

    async def event_generator():
        # Send session_id first
        yield f"event: session\ndata: {sid}\n\n"
        # Stream the chat response
        async for event in stream_fn(sid, req.message):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
