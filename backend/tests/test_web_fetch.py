"""URL 抓取：SSRF 校验、重定向防护、大小限制、正文提取与入库链路。"""
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.services.ingestion.web_fetch import (
    UrlFetchError,
    extract_markdown,
    fetch_url_to_doc,
    validate_url,
)

FAKE_DNS = {"example.com": "93.184.216.34", "redirect.example.com": "93.184.216.34"}


@pytest.fixture
def fake_dns(monkeypatch):
    def _install(mapping=None):
        mapping = mapping or FAKE_DNS

        def fake_getaddrinfo(host, port, *args, **kwargs):
            ip = mapping.get(host)
            if ip is None:
                # 直接给 IP 字面量的情况，原样返回
                ip = host
            import socket as _socket

            return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", (ip, port or 0))]

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)

    return _install


@pytest.fixture
def ws_with_fake_embedding():
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-url-{uuid4()}")
        s.add(ws)
        provider = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                  is_default=True, params={"dim": 4})
        s.add(provider)
        s.commit()
        yield ws
        s.delete(provider)
        s.delete(ws)
        s.commit()


def _cleanup_doc(doc_id: int) -> None:
    import pathlib

    from app.services.ingestion.pipeline import doc_file_path

    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if doc:
            path = doc_file_path(doc)
            s.delete(doc)
            s.commit()
    pathlib.Path(path).unlink(missing_ok=True)


# ---------------- validate_url ----------------

@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "http://localhost/admin",
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://169.254.1.1/",
    "http://0.0.0.0/",
])
def test_validate_url_rejects(url, fake_dns):
    fake_dns()
    with pytest.raises(UrlFetchError):
        validate_url(url)


def test_validate_url_accepts_public(fake_dns):
    fake_dns()
    assert validate_url("https://example.com/page") is None


def test_validate_url_accepts_http(fake_dns):
    fake_dns()
    assert validate_url("http://example.com/") is None


def test_validate_url_rejects_bad_scheme():
    with pytest.raises(UrlFetchError):
        validate_url("javascript:alert(1)")


# ---------------- HTML 提取 ----------------

def test_extract_markdown_prefers_article():
    html = """<html><head><title>页面标题</title></head><body>
    <nav>导航噪音</nav>
    <article><h1>文章标题</h1><p>第一段内容。</p><p>第二段内容。</p></article>
    <footer>页脚噪音</footer><script>evil()</script></body></html>"""
    title, text = extract_markdown(html)
    assert title == "页面标题"
    assert "第一段内容。" in text
    assert "第二段内容。" in text
    assert "导航噪音" not in text
    assert "页脚噪音" not in text
    assert "evil" not in text


def test_extract_markdown_falls_back_to_body():
    html = "<html><head><title>T</title></head><body><p>只有正文段落。</p></body></html>"
    title, text = extract_markdown(html)
    assert title == "T"
    assert text == "只有正文段落。"


# ---------------- 抓取 ----------------

HTML_PAGE = """<html><head><title>示例页面</title></head><body>
<article><p>这是正文第一段，内容足够。</p><p>这是正文第二段。</p></article></body></html>""".encode()


async def test_fetch_creates_pending_doc_and_ingests(ws_with_fake_embedding, httpx_mock, fake_dns):
    from app.services.ingestion.pipeline import ingest_document

    fake_dns()
    httpx_mock.add_response(url="https://example.com/doc", status_code=200, content=HTML_PAGE,
                            headers={"content-type": "text/html"})
    doc = await fetch_url_to_doc(ws_with_fake_embedding.id, "https://example.com/doc")
    try:
        assert doc.status == "pending"
        assert doc.source_type == "url"
        assert doc.filename == "example.com_doc.md"
        assert doc.mime == "text/markdown"
        with SessionLocal() as s:
            from app.services.ingestion.pipeline import doc_file_path

            d = s.get(Document, doc.id)
            content = doc_file_path(d).read_text(encoding="utf-8")
            assert content.startswith("# 示例页面")
            assert "这是正文第一段，内容足够。" in content
            assert "这是正文第二段。" in content
        await ingest_document(doc.id)
        with SessionLocal() as s:
            d = s.get(Document, doc.id)
            assert d.status == "ready"
            assert d.error is None
            chunks = s.execute(select(Chunk).where(Chunk.document_id == doc.id)).scalars().all()
            assert len(chunks) > 0
            emb = s.execute(select(func.count()).select_from(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id.in_([c.id for c in chunks]))).scalar()
            assert emb == len(chunks)
    finally:
        _cleanup_doc(doc.id)


async def test_fetch_rejects_redirect_to_private(ws_with_fake_embedding, httpx_mock, fake_dns):
    fake_dns({"example.com": "93.184.216.34", "internal.example.com": "127.0.0.1"})
    httpx_mock.add_response(url="https://example.com/jump", status_code=302,
                            headers={"location": "http://internal.example.com/secret"})
    with pytest.raises(UrlFetchError, match="内网"):
        await fetch_url_to_doc(ws_with_fake_embedding.id, "https://example.com/jump")


async def test_fetch_rejects_over_10mb(ws_with_fake_embedding, httpx_mock, fake_dns):
    fake_dns()
    httpx_mock.add_response(url="https://example.com/big", status_code=200,
                            headers={"content-length": str(11 * 1024 * 1024),
                                     "content-type": "text/html"},
                            content=b"x")
    with pytest.raises(UrlFetchError, match="10MB"):
        await fetch_url_to_doc(ws_with_fake_embedding.id, "https://example.com/big")


async def test_fetch_rejects_http_404(ws_with_fake_embedding, httpx_mock, fake_dns):
    fake_dns()
    httpx_mock.add_response(url="https://example.com/missing", status_code=404)
    with pytest.raises(UrlFetchError, match="404"):
        await fetch_url_to_doc(ws_with_fake_embedding.id, "https://example.com/missing")


# ---------------- API ----------------

def test_api_url_endpoint_success(client, ws_with_fake_embedding, httpx_mock, fake_dns):
    fake_dns()
    httpx_mock.add_response(url="https://example.com/api-doc", status_code=200, content=HTML_PAGE,
                            headers={"content-type": "text/html"})
    resp = client.post(f"/api/workspaces/{ws_with_fake_embedding.id}/documents/url",
                       json={"url": "https://example.com/api-doc"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["source_type"] == "url"
    # 响应体是任务前快照；TestClient 中后台任务同步执行，查库应为 ready
    with SessionLocal() as s:
        assert s.get(Document, body["id"]).status == "ready"
    _cleanup_doc(body["id"])


def test_api_url_endpoint_rejects_private(client, ws_with_fake_embedding, fake_dns):
    fake_dns()
    resp = client.post(f"/api/workspaces/{ws_with_fake_embedding.id}/documents/url",
                       json={"url": "http://127.0.0.1/"})
    assert resp.status_code == 400
    assert "内网" in resp.json()["detail"]


def test_api_url_endpoint_ws_404(client):
    resp = client.post("/api/workspaces/999999/documents/url",
                       json={"url": "https://example.com/"})
    assert resp.status_code == 404
