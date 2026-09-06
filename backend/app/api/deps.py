"""API 层共用小助手。"""
from fastapi import HTTPException
from sqlalchemy.orm import Session


def get_or_404(s: Session, model, id_, detail: str):
    """按主键取实体，不存在则 404。"""
    obj = s.get(model, id_)
    if not obj:
        raise HTTPException(status_code=404, detail=detail)
    return obj
