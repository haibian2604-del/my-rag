import uuid

from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.models.entities import Document, Workspace


def test_tables_exist_and_relationships():
    with SessionLocal() as s:
        ws = Workspace(name=f"test-ws-{uuid.uuid4().hex[:8]}")
        s.add(ws)
        s.flush()
        doc = Document(workspace_id=ws.id, filename="a.pdf", source_type="upload",
                       mime="application/pdf", size=1, checksum="abc", status="pending")
        s.add(doc)
        s.commit()
        assert doc.id > 0
        # pgvector 扩展可用
        with engine.connect() as c:
            assert c.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar() == "vector"
        s.delete(doc); s.delete(ws); s.commit()
