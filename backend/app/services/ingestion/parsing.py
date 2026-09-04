# app/services/ingestion/parsing.py
"""文档解析：将 md/txt/pdf/docx 统一解析为 Block 列表。

Block = {"text": str, "page_no": int | None, "heading_path": str}
"""
from pathlib import Path

import fitz  # pymupdf
from docx import Document as DocxDocument
from markdown_it import MarkdownIt

md_parser = MarkdownIt()

SUPPORTED = {".md", ".txt", ".pdf", ".docx"}


def parse_file(path: Path, mime: str) -> list[dict]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的文件类型: {ext}")
    return {
        ".md": _parse_markdown,
        ".txt": _parse_text,
        ".pdf": _parse_pdf,
        ".docx": _parse_docx,
    }[ext](path)


def _parse_markdown(path: Path) -> list[dict]:
    tokens = md_parser.parse(path.read_text(encoding="utf-8"))
    blocks: list[dict] = []
    headings: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = "".join(buf).strip()
        if text:
            blocks.append({"text": text, "page_no": None, "heading_path": " > ".join(headings)})
        buf.clear()

    # 配对遍历：heading_open 后紧跟的 inline token 是标题文本
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            flush()
            level = int(tok.tag[1])  # h1..h6 -> 1..6
            del headings[level - 1:]
            # 下一个应为 inline，作为标题文本
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                headings.append(tokens[i + 1].content.strip())
                i += 1
        elif tok.type == "inline":
            buf.append(tok.content + "\n\n")
        i += 1
    flush()
    return blocks


def _parse_text(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    blocks: list[dict] = []
    for para in _split_paragraphs(content):
        blocks.append({"text": para, "page_no": None, "heading_path": ""})
    return blocks


def _split_paragraphs(content: str) -> list[str]:
    import re

    paras = []
    for raw in re.split(r"\n\s*\n", content):
        text = raw.strip()
        if text:
            paras.append(text)
    return paras


def _parse_pdf(path: Path) -> list[dict]:
    blocks: list[dict] = []
    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            for text in _split_paragraphs(page.get_text()):
                blocks.append({"text": text, "page_no": page_no, "heading_path": ""})
    return blocks


def _parse_docx(path: Path) -> list[dict]:
    doc = DocxDocument(path)
    blocks: list[dict] = []
    headings: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style is not None else ""
        if style.startswith("Heading"):
            try:
                level = int(style.split()[-1])
            except ValueError:
                level = len(headings) + 1
            del headings[level - 1:]
            headings.append(text)
        else:
            blocks.append({"text": text, "page_no": None, "heading_path": " > ".join(headings)})
    return blocks
