import asyncio
import uuid

from pydantic import BaseModel

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.agent import chat as agent_chat
from app.services.agent import chat_stream as agent_chat_stream

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    reply = await asyncio.to_thread(agent_chat, sid, req.message)
    return {"reply": reply, "session_id": sid}


@router.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    """Streaming chat endpoint using SSE (Server-Sent Events)."""
    sid = req.session_id or str(uuid.uuid4())

    async def event_generator():
        # Send session_id first
        yield f"event: session\ndata: {sid}\n\n"
        # Stream the chat response
        async for event in agent_chat_stream(sid, req.message):
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
