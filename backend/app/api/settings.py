import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.auth import get_auth_config, require_auth, set_auth_config
from app.core.db import SessionLocal
from app.core.security import encrypt_secret
from app.models.entities import ProviderConfig, Workspace
from app.services.providers_service import (
    clear_other_defaults,
    get_provider_or_404,
    mask_api_key,
    provider_api_key,
)

router = APIRouter(dependencies=[Depends(require_auth)])

TEST_TIMEOUT = 15.0
ERR_TRUNCATE = 500


class ProviderIn(BaseModel):
    kind: str  # llm|embedding|rerank
    provider: str = "openai_compat"
    base_url: str
    model: str
    api_key: str | None = None
    is_default: bool = False
    params: dict = Field(default_factory=dict)


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    provider: str
    base_url: str
    model: str
    api_key: str | None = None  # 掩码回显
    is_default: bool
    params: dict


class ProviderTestIn(BaseModel):
    kind: str
    base_url: str
    model: str
    api_key: str | None = None


class AppSettingsIn(BaseModel):
    auth_enabled: bool
    password: str | None = None


def _err_detail(exc: Exception) -> str:
    msg = str(exc) or exc.__class__.__name__
    return msg[:ERR_TRUNCATE]


def _auth_headers(api_key: str | None) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


@router.get("/settings/providers", response_model=list[ProviderOut])
def list_providers():
    with SessionLocal() as s:
        rows = s.execute(
            select(ProviderConfig).order_by(ProviderConfig.id)
        ).scalars().all()
        return [
            ProviderOut(
                id=p.id, kind=p.kind, provider=p.provider, base_url=p.base_url,
                model=p.model, api_key=mask_api_key(p), is_default=p.is_default,
                params=p.params,
            )
            for p in rows
        ]


@router.post("/settings/providers", response_model=ProviderOut, status_code=201)
def create_provider(body: ProviderIn):
    with SessionLocal() as s:
        p = ProviderConfig(
            kind=body.kind, provider=body.provider, base_url=body.base_url,
            model=body.model,
            api_key_encrypted=encrypt_secret(body.api_key) if body.api_key else None,
            is_default=body.is_default, params=body.params,
        )
        if body.is_default:
            clear_other_defaults(s, body.kind)
        s.add(p)
        s.commit()
        return ProviderOut(
            id=p.id, kind=p.kind, provider=p.provider, base_url=p.base_url,
            model=p.model, api_key=mask_api_key(p), is_default=p.is_default,
            params=p.params,
        )


@router.put("/settings/providers/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: int, body: ProviderIn):
    with SessionLocal() as s:
        p = get_provider_or_404(s, provider_id)
        p.kind = body.kind
        p.provider = body.provider
        p.base_url = body.base_url
        p.model = body.model
        if body.api_key:  # 不传/留空则保留旧 key
            p.api_key_encrypted = encrypt_secret(body.api_key)
        p.is_default = body.is_default
        p.params = body.params
        if body.is_default:
            clear_other_defaults(s, body.kind, exclude_id=p.id)
        s.commit()
        return ProviderOut(
            id=p.id, kind=p.kind, provider=p.provider, base_url=p.base_url,
            model=p.model, api_key=mask_api_key(p), is_default=p.is_default,
            params=p.params,
        )


@router.delete("/settings/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int):
    with SessionLocal() as s:
        p = get_provider_or_404(s, provider_id)
        s.delete(p)
        s.commit()


@router.post("/settings/providers/test")
def test_provider(body: ProviderTestIn):
    if body.kind == "rerank":
        raise HTTPException(status_code=501, detail="rerank 测试将在 M3 支持")
    try:
        with httpx.Client(timeout=TEST_TIMEOUT) as client:
            if body.kind == "llm":
                resp = client.post(
                    f"{body.base_url}/chat/completions",
                    json={"model": body.model,
                          "messages": [{"role": "user", "content": "ping"}],
                          "max_tokens": 8},
                    headers=_auth_headers(body.api_key),
                )
                if resp.status_code >= 300:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:ERR_TRUNCATE]}")
                data = resp.json()
                if not data.get("choices"):
                    raise RuntimeError("响应缺少 choices")
                return {"ok": True}
            if body.kind == "embedding":
                resp = client.post(
                    f"{body.base_url}/embeddings",
                    json={"model": body.model, "input": ["测试"]},
                    headers=_auth_headers(body.api_key),
                )
                if resp.status_code >= 300:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:ERR_TRUNCATE]}")
                data = resp.json()
                embedding = data["data"][0]["embedding"]
                return {"ok": True, "dim": len(embedding)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_err_detail(exc)) from None
    raise HTTPException(status_code=422, detail=f"不支持的 kind: {body.kind}")


@router.get("/settings/models")
def discover_models(
    base_url: str = Query(...),
    api_key: str | None = Query(default=None),
):
    try:
        with httpx.Client(timeout=TEST_TIMEOUT) as client:
            resp = client.get(f"{base_url}/models", headers=_auth_headers(api_key))
            resp.raise_for_status()
            data = resp.json()
            models = [m["id"] for m in data.get("data", []) if m.get("id")]
            return {"models": models}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_err_detail(exc)) from None


@router.get("/settings/app")
def get_app_settings():
    with SessionLocal() as s:
        cfg = get_auth_config(s)
    return {"auth_enabled": cfg["enabled"]}


@router.put("/settings/app")
def update_app_settings(body: AppSettingsIn):
    if body.auth_enabled and not body.password:
        raise HTTPException(status_code=422, detail="开启认证时必须设置密码")
    with SessionLocal() as s:
        set_auth_config(s, enabled=body.auth_enabled,
                        password=body.password if body.auth_enabled else None)
    return {"auth_enabled": body.auth_enabled}


# ---------- workspace 检索参数 ----------

RETRIEVAL_DEFAULTS = {
    "top_k": 5,
    "score_threshold": 0.0,
    "use_rerank": True,
    "context_max_tokens": 3000,
}


class WorkspaceSettingsIn(BaseModel):
    top_k: int = Field(ge=1, le=20)
    score_threshold: float = Field(ge=0.0, le=1.0)
    use_rerank: bool = True
    context_max_tokens: int = Field(ge=500, le=8000)


@router.get("/workspaces/{ws_id}/settings")
def get_workspace_settings(ws_id: int):
    with SessionLocal() as s:
        ws = s.get(Workspace, ws_id)
        if not ws:
            raise HTTPException(status_code=404, detail="workspace 不存在")
        merged = {**RETRIEVAL_DEFAULTS, **(ws.params or {})}
        return merged


@router.put("/workspaces/{ws_id}/settings")
def update_workspace_settings(ws_id: int, body: WorkspaceSettingsIn):
    with SessionLocal() as s:
        ws = s.get(Workspace, ws_id)
        if not ws:
            raise HTTPException(status_code=404, detail="workspace 不存在")
        ws.params = body.model_dump()
        s.commit()
        return ws.params
