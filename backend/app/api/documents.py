import hashlib
from pathlib import Path as FsPath

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from app.api.deps import get_or_404
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import Document, Workspace
from app.services.ingestion.pipeline import doc_file_path
from app.services.ingestion.web_fetch import UrlFetchError, fetch_url_to_doc

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
    summary: str | None = None  # 自动摘要（M5-T2），未生成时为 None


class DocumentDetailOut(DocumentOut):
    """文档详情：额外带原文预览（前 500 字符，文件缺失时为空串）。"""

    preview: str = ""


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
        get_or_404(s, Workspace, ws_id, "workspace 不存在")
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


class UrlIn(BaseModel):
    url: str = Field(min_length=1)


@router.post("/workspaces/{ws_id}/documents/url", response_model=DocumentOut, status_code=201)
async def fetch_url_document(ws_id: int, body: UrlIn, background_tasks: BackgroundTasks):
    with SessionLocal() as s:
        get_or_404(s, Workspace, ws_id, "workspace 不存在")
    try:
        doc = await fetch_url_to_doc(ws_id, body.url)
    except UrlFetchError as e:
        raise HTTPException(status_code=400, detail=str(e)[:500])
    background_tasks.add_task(run_ingestion_sync, doc.id)
    return doc


@router.get("/workspaces/{ws_id}/documents", response_model=list[DocumentOut])
def list_documents(ws_id: int):
    with SessionLocal() as s:
        get_or_404(s, Workspace, ws_id, "workspace 不存在")
        docs = s.execute(
            select(Document).where(Document.workspace_id == ws_id).order_by(Document.id)
        ).scalars().all()
        return list(docs)


def _read_preview(doc: Document) -> str:
    """读原文文件前 500 字符作为预览；文件不存在/读取失败返回空串。"""
    try:
        text = doc_file_path(doc).read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return ""
    return text[:500]


@router.get("/documents/{doc_id}", response_model=DocumentDetailOut)
def get_document(doc_id: int):
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document 不存在")
        out = DocumentDetailOut.model_validate(doc)
        out.preview = _read_preview(doc)
        return out


@router.post("/documents/{doc_id}/summary", response_model=DocumentOut)
async def regenerate_summary(doc_id: int):
    """手动（重新）生成摘要：失败/无 LLM 降级为不更新 summary，接口不报错。"""
    with SessionLocal() as s:
        if not s.get(Document, doc_id):
            raise HTTPException(status_code=404, detail="document 不存在")
    from app.services.summary_service import generate_document_summary
    # 内部已吞掉所有失败（返回 None），这里兜底不抛
    await generate_document_summary(doc_id)
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
