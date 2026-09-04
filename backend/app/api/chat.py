from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import Conversation, Message, Workspace
from app.services.chat.service import ask_stream, get_llm_or_raise
from app.services.chat.service import LLMNotConfiguredError

router = APIRouter(dependencies=[Depends(require_auth)])


class ConversationOut(BaseModel):
    id: int
    workspace_id: int
    title: str


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    citations: list | None = None


class AskIn(BaseModel):
    question: str


def _get_conv(s, conv_id: int) -> Conversation:
    conv = s.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.post("/workspaces/{ws_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(ws_id: int):
    with SessionLocal() as s:
        ws = s.get(Workspace, ws_id)
        if not ws:
            raise HTTPException(status_code=404, detail="workspace 不存在")
        conv = Conversation(workspace_id=ws_id)
        s.add(conv)
        s.commit()
        s.refresh(conv)
        return conv


@router.get("/conversations/{conv_id}/messages", response_model=list[MessageOut])
def list_messages(conv_id: int):
    with SessionLocal() as s:
        _get_conv(s, conv_id)
        rows = s.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).scalars().all()
        return rows


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int):
    with SessionLocal() as s:
        s.delete(_get_conv(s, conv_id))
        s.commit()


@router.post("/conversations/{conv_id}/ask")
def ask(conv_id: int, body: AskIn):
    with SessionLocal() as s:
        _get_conv(s, conv_id)
        try:
            get_llm_or_raise(s)
        except LLMNotConfiguredError:
            raise HTTPException(status_code=400, detail="未配置 LLM 模型") from None
    return StreamingResponse(
        ask_stream(conv_id, body.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
