"""启动恢复测试：重嵌 running 状态因进程重启恢复为 failed。"""
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.jobs import runner
from app.jobs.runner import recover_interrupted
from app.models.entities import AppConfig
from app.services.embedding_switch import read_state, write_state


def test_recover_interrupted_resets_running_reembed(monkeypatch):
    # 共享库可能有遗留 pending 文档，重跑不实际执行
    monkeypatch.setattr(runner, "run_ingestion_sync", lambda doc_id: None)
    with SessionLocal() as s:
        write_state(s, state="running", target_model="some-model", done=3, error=None)
        s.commit()
    try:
        recover_interrupted()
        with SessionLocal() as s:
            state = read_state(s)
            assert state["state"] == "failed"
            assert state["error"] == "服务重启中断重嵌"
    finally:
        with SessionLocal() as s:
            s.execute(delete(AppConfig).where(AppConfig.key == "embedding_switch"))
            s.commit()


def test_recover_interrupted_keeps_non_running_state(monkeypatch):
    monkeypatch.setattr(runner, "run_ingestion_sync", lambda doc_id: None)
    with SessionLocal() as s:
        write_state(s, state="done", target_model="m", done=5)
        s.commit()
    try:
        recover_interrupted()
        with SessionLocal() as s:
            assert read_state(s)["state"] == "done"
    finally:
        with SessionLocal() as s:
            s.execute(delete(AppConfig).where(AppConfig.key == "embedding_switch"))
            s.commit()
