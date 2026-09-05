"""URL 抓取入库：SSRF 防护校验 → 手动重定向循环 → HTML 正文提取 → 落盘为 markdown。

SSRF 硬约束：仅 http/https；每跳对主机做 DNS 解析，
任一 IP 属 private/loopback/link-local/reserved/multicast/unspecified 即拒绝。
"""
import hashlib
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.db import SessionLocal
from app.models.entities import Document
from app.services.ingestion.pipeline import doc_file_path

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10MB
MAX_REDIRECTS = 5
TIMEOUT = 15.0

_BLOCKED_REASONS = (
    "is_private", "is_loopback", "is_link_local",
    "is_reserved", "is_multicast", "is_unspecified",
)


class UrlFetchError(Exception):
    """校验/抓取失败，消息可直接作为 400 detail（中文）。"""


def validate_url(url: str) -> None:
    """校验 URL 安全性，不通过则抛 UrlFetchError。"""
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise UrlFetchError(f"URL 无效: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise UrlFetchError(f"仅支持 http/https 协议，收到: {parsed.scheme or '(空)'}")
    if not parsed.hostname:
        raise UrlFetchError("URL 缺少主机名")
    try:
        port = parsed.port
    except ValueError as e:
        raise UrlFetchError(f"URL 端口无效: {e}") from e

    for ip in _resolve_host(parsed.hostname, port):
        if any(getattr(ip, attr) for attr in _BLOCKED_REASONS):
            raise UrlFetchError(f"拒绝访问内网/保留地址: {ip}")


def _resolve_host(hostname: str, port: int | None) -> list:
    """解析主机的全部 IP，任一次解析失败即拒绝。"""
    try:
        infos = socket.getaddrinfo(hostname, port)
    except socket.gaierror as e:
        raise UrlFetchError(f"域名解析失败: {hostname}") from e
    ips = []
    for info in infos:
        addr = info[4][0]
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError as e:
            raise UrlFetchError(f"无法识别的地址: {addr}") from e
    if not ips:
        raise UrlFetchError(f"域名解析不到地址: {hostname}")
    return ips


def _sanitize_filename(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "site"
    last_seg = [s for s in parsed.path.split("/") if s]
    tail = last_seg[-1] if last_seg else "index"
    name = f"{host}_{tail}"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "index"
    return name[:100] + ".md"


def extract_markdown(html: str) -> tuple[str, str]:
    """从 HTML 提取 (标题, 正文)。正文按空行分段，适配 .txt 段落语义。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    container = soup.find("article") or soup.find("main") or soup.body or soup

    title = ""
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)
    else:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    raw = container.get_text(separator="\n")
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    return title, "\n\n".join(paras)


def _parse_page(body: bytes, content_type: str) -> str:
    text = body.decode("utf-8", errors="replace")
    if "html" in (content_type or "").lower() or not content_type:
        title, body_text = extract_markdown(text)
        if not body_text:
            raise UrlFetchError("页面未提取到正文内容")
        return f"# {title}\n\n{body_text}" if title else body_text
    # 非 HTML：按纯文本处理
    text = text.strip()
    if not text:
        raise UrlFetchError("页面未提取到正文内容")
    return text


async def fetch_url_to_doc(ws_id: int, url: str) -> Document:
    """校验并抓取 URL，提取正文落盘为 markdown 文档（pending，待后台摄取）。"""
    validate_url(url)
    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=TIMEOUT) as client:
        for _ in range(MAX_REDIRECTS):
            try:
                resp = await client.send(client.build_request("GET", current), stream=True)
            except httpx.HTTPError as e:
                raise UrlFetchError(f"抓取失败: {e}") from e
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                await resp.aclose()
                if not location:
                    raise UrlFetchError(f"重定向缺少 Location: {resp.status_code}")
                current = urljoin(current, location)
                validate_url(current)
                continue
            if resp.status_code >= 400:
                await resp.aclose()
                raise UrlFetchError(f"抓取失败: HTTP {resp.status_code}")
            break
        else:
            raise UrlFetchError(f"重定向超过 {MAX_REDIRECTS} 跳")

        try:
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
                raise UrlFetchError("响应体超过 10MB 限制")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise UrlFetchError("响应体超过 10MB 限制")
                chunks.append(chunk)
        finally:
            await resp.aclose()
    body = b"".join(chunks)

    md = _parse_page(body, resp.headers.get("content-type", ""))
    md_bytes = md.encode("utf-8")

    doc = Document(workspace_id=ws_id, filename=_sanitize_filename(current),
                   source_type="url", mime="text/markdown", size=len(md_bytes),
                   checksum=hashlib.sha256(md_bytes).hexdigest(), status="pending")
    with SessionLocal() as s:
        s.add(doc)
        s.flush()
        path: Path = doc_file_path(doc)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(md_bytes)
        s.commit()
    return doc
