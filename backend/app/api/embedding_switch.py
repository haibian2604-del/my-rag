"""嵌入模型切换/回滚 API：验证向量存在后才切换默认 provider。"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import ChunkEmbedding
from app.services.embedding_switch import read_state, write_state
from app.services.ingestion.pipeline import get_default_provider

router = APIRouter(dependencies=[Depends(require_auth)])


class SwitchIn(BaseModel):
    target_model: str = Field(min_length=1)


class ActivateIn(BaseModel):
    model: str = Field(min_length=1)


@router.post("/settings/embedding/switch", status_code=202)
def switch_embedding(body: SwitchIn, background_tasks: BackgroundTasks):
    from app.jobs.runner import run_reembed_sync

    with SessionLocal() as s:
        try:
            current = get_default_provider(s, "embedding")
        except RuntimeError:
            raise HTTPException(status_code=404, detail="未配置嵌入模型") from None
        if body.target_model == current.model:
            raise HTTPException(status_code=400, detail="目标模型与当前一致")
        state = read_state(s)
        if state.get("state") == "running":
            raise HTTPException(status_code=409, detail="已有重嵌任务在运行")
        write_state(s, state="running", target_model=body.target_model,
                    done=0, error=None)
        s.commit()
    # total 由 reembed_all 启动后写入；不自动切换 provider
    background_tasks.add_task(run_reembed_sync, body.target_model)
    return {"state": "running", "target_model": body.target_model}


@router.get("/settings/embedding/switch")
def get_switch_state():
    with SessionLocal() as s:
        state = read_state(s)
        try:
            current_model = get_default_provider(s, "embedding").model
        except RuntimeError:
            current_model = None
    out = {
        "state": state.get("state", "idle"),
        "current_model": current_model,
    }
    for key in ("target_model", "total", "done", "error"):
        if state.get(key) is not None:
            out[key] = state[key]
    return out


def _activate_model(s, model: str) -> None:
    """校验向量存在后切换默认 embedding provider 的 model，并记录 previous_model。"""
    has_vector = s.execute(
        select(ChunkEmbedding.id).where(ChunkEmbedding.model_name == model).limit(1)
    ).scalar_one_or_none()
    if has_vector is None:
        raise HTTPException(status_code=404, detail="该模型暂无可用向量")
    try:
        provider = get_default_provider(s, "embedding")
    except RuntimeError:
        raise HTTPException(status_code=404, detail="未配置嵌入模型") from None
    previous_model = provider.model
    provider.model = model  # 仅改 model，保持 api_key/base_url/params 不变
    write_state(s, previous_model=previous_model)
    s.commit()


@router.post("/settings/embedding/activate")
def activate_embedding(body: ActivateIn):
    with SessionLocal() as s:
        _activate_model(s, body.model)
    return {"current_model": body.model}


@router.post("/settings/embedding/rollback")
def rollback_embedding(body: ActivateIn):
    with SessionLocal() as s:
        _activate_model(s, body.model)
    return {"current_model": body.model}
