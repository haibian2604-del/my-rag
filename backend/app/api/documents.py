import hashlib
from pathlib import Path as FsPath

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import Document, Workspace
from app.services.ingestion.pipeline import doc_file_path

router = APIRouter(dependencies=[Depends(require_auth)])

ALLOWED_EXTS = {".md", ".txt", ".pdf", ".docx"}
MAX_SIZE = 50 * 1024 * 1024  # 50MB


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    filename: str
    source_type: str
    mime: str
    size: int
    checksum: str
    status: str
    error: str | None


def _get_ws(s, ws_id: int) -> Workspace:
    ws = s.get(Workspace, ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="workspace 不存在")
    return ws


@router.post("/workspaces/{ws_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(ws_id: int, background_tasks: BackgroundTasks,
                          file: UploadFile = File(...)):  # noqa: B008
    filename = FsPath(file.filename or "").name
    ext = FsPath(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=422, detail=f"不支持的文件类型: {ext or '(无后缀)'}")
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=422, detail="文件超过 50MB 限制")
    checksum = hashlib.sha256(content).hexdigest()
    mime = file.content_type or ""

    with SessionLocal() as s:
        _get_ws(s, ws_id)
        doc = Document(workspace_id=ws_id, filename=filename, source_type="upload",
                       mime=mime, size=len(content), checksum=checksum, status="pending")
        s.add(doc)
        s.flush()  # 先拿 id 再落盘
        path = doc_file_path(doc)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError:
            s.rollback()
            raise HTTPException(status_code=500, detail="文件写入失败")
        s.commit()
        background_tasks.add_task(run_ingestion_sync, doc.id)
        return doc


@router.get("/workspaces/{ws_id}/documents", response_model=list[DocumentOut])
def list_documents(ws_id: int):
    with SessionLocal() as s:
        _get_ws(s, ws_id)
        docs = s.execute(
            select(Document).where(Document.workspace_id == ws_id).order_by(Document.id)
        ).scalars().all()
        return list(docs)


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: int):
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document 不存在")
        return doc


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int):
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document 不存在")
        path = doc_file_path(doc)
        s.delete(doc)
        s.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return Response(status_code=204)


@router.post("/documents/{doc_id}/reingest", response_model=DocumentOut)
def reingest_document(doc_id: int, background_tasks: BackgroundTasks):
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document 不存在")
        doc.status = "pending"
        doc.error = None
        s.commit()
    background_tasks.add_task(run_ingestion_sync, doc.id)
    return doc
