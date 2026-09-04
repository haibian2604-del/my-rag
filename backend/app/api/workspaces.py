from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import Workspace

router = APIRouter(dependencies=[Depends(require_auth)])


class WorkspaceOut(BaseModel):
    id: int
    name: str
    description: str


class WorkspaceIn(BaseModel):
    name: str
    description: str = ""


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
