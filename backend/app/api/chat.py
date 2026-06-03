import uuid

from pydantic import BaseModel

from fastapi import APIRouter

from app.services.agent import chat as agent_chat

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    reply = agent_chat(sid, req.message)
    return {"reply": reply, "session_id": sid}
