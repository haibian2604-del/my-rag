from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_or_404
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import Document, Workspace
from app.services.ingestion.pipeline import doc_file_path
from app.services.chat.suggestions import get_suggestions

router = APIRouter(dependencies=[Depends(require_auth)])


class WorkspaceOut(BaseModel):
    id: int
    name: str
    description: str


def _check_workspace_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("工作区名称不能为空")
    if len(v) > 100:
        raise ValueError("工作区名称过长（≤100）")
    return v


class WorkspaceIn(BaseModel):
    name: str
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name_valid(cls, v: str) -> str:
        return _check_workspace_name(v)


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces():
    with SessionLocal() as s:
        rows = s.execute(select(Workspace).order_by(Workspace.id)).scalars().all()
        return rows


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceIn):
    with SessionLocal() as s:
        exists = s.execute(
            select(Workspace).where(Workspace.name == body.name)
        ).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=409, detail="同名工作区已存在")
        ws = Workspace(name=body.name, description=body.description)
        s.add(ws)
        s.commit()
        s.refresh(ws)
        return ws


@router.put("/workspaces/{ws_id}", response_model=WorkspaceOut)
def update_workspace(ws_id: int, body: WorkspaceIn):
    with SessionLocal() as s:
        ws = get_or_404(s, Workspace, ws_id, "工作区不存在")
        dup = s.execute(
            select(Workspace).where(Workspace.name == body.name, Workspace.id != ws_id)
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail="同名工作区已存在")
        ws.name = body.name
        ws.description = body.description
        s.commit()
        s.refresh(ws)
        return ws


@router.delete("/workspaces/{ws_id}", status_code=204)
def delete_workspace(ws_id: int):
    with SessionLocal() as s:
        ws = get_or_404(s, Workspace, ws_id, "工作区不存在")
        total = s.execute(select(func.count()).select_from(Workspace)).scalar_one()
        if total <= 1:
            raise HTTPException(status_code=409, detail="至少保留一个工作区")
        # 先收集磁盘文件路径（复用 pipeline.doc_file_path），commit 后逐个回收
        doc_rows = s.execute(
            select(Document).where(Document.workspace_id == ws_id)
        ).scalars().all()
        paths = [doc_file_path(d) for d in doc_rows]
        s.delete(ws)  # 关联文档/会话等靠 FK ondelete=CASCADE 级联删除
        s.commit()
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # 磁盘回收失败不阻塞响应


@router.get("/workspaces/{ws_id}/suggestions")
async def get_suggestions_api(ws_id: int):
    """工作区建议问题：无文档/未配置 LLM/生成失败一律返回空数组（绝不 500）。"""
    with SessionLocal() as s:
        get_or_404(s, Workspace, ws_id, "工作区不存在")
    try:
        questions = await get_suggestions(ws_id)
    except Exception:  # noqa: BLE001 — 生成类能力失败降级为空
        questions = []
    return {"questions": questions}
