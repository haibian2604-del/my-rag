from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import get_or_404
from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import Conversation, Message, Workspace
from app.services.agent.runner import agent_stream
from app.services.chat.service import ask_stream
from app.services.ingestion.pipeline import get_default_provider

router = APIRouter(dependencies=[Depends(require_auth)])


class ConversationOut(BaseModel):
    id: int
    workspace_id: int
    title: str
    created_at: datetime | None = None


class ConversationUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空")
        return v


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    citations: list | None = None
    trace: list | None = None  # agent 模式的工具调用时间线（RAG 模式为 NULL）


class AskIn(BaseModel):
    question: str
    mode: str = "rag"  # rag（默认，零行为变化）| agent（ReAct 工具问答）


@router.post("/workspaces/{ws_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(ws_id: int):
    with SessionLocal() as s:
        get_or_404(s, Workspace, ws_id, "workspace 不存在")
        conv = Conversation(workspace_id=ws_id)
        s.add(conv)
        s.commit()
        s.refresh(conv)
        return conv


@router.get("/workspaces/{ws_id}/conversations", response_model=list[ConversationOut])
def list_conversations(ws_id: int):
    with SessionLocal() as s:
        get_or_404(s, Workspace, ws_id, "workspace 不存在")
        rows = s.execute(
            select(Conversation).where(Conversation.workspace_id == ws_id)
            .order_by(Conversation.id.desc())
        ).scalars().all()
        return rows


@router.put("/conversations/{conv_id}", response_model=ConversationOut)
def update_conversation(conv_id: int, body: ConversationUpdateIn):
    with SessionLocal() as s:
        conv = get_or_404(s, Conversation, conv_id, "会话不存在")
        conv.title = body.title
        s.commit()
        s.refresh(conv)
        return conv


@router.get("/conversations/{conv_id}/messages", response_model=list[MessageOut])
def list_messages(conv_id: int):
    with SessionLocal() as s:
        get_or_404(s, Conversation, conv_id, "会话不存在")
        rows = s.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ).scalars().all()
        return rows


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int):
    with SessionLocal() as s:
        s.delete(get_or_404(s, Conversation, conv_id, "会话不存在"))
        s.commit()


@router.post("/conversations/{conv_id}/ask")
def ask(conv_id: int, body: AskIn):
    if body.mode not in ("rag", "agent"):
        raise HTTPException(status_code=400, detail="mode 仅支持 rag 或 agent")
    with SessionLocal() as s:
        get_or_404(s, Conversation, conv_id, "会话不存在")
        try:
            get_default_provider(s, "llm")
        except RuntimeError:
            raise HTTPException(status_code=400, detail="未配置 LLM 模型") from None
    # 按 mode 分流：agent 走 PydanticAI ReAct 运行器；rag 保持原链路零行为变化
    stream = (agent_stream(conv_id, body.question) if body.mode == "agent"
              else ask_stream(conv_id, body.question))
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
