import pytest

from app.services.ingestion.parsing import parse_file


def test_markdown_headings(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 总览\nintro\n\n## 安装\nrun pip install\n", encoding="utf-8")
    blocks = parse_file(p, "text/markdown")
    assert blocks[0]["heading_path"] == "总览"
    assert blocks[1]["heading_path"] == "总览 > 安装"
    assert "pip install" in blocks[1]["text"]


def test_unsupported_type(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        parse_file(p, "application/octet-stream")


def test_text_paragraphs(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("第一段。\n\n\n第二段。\n", encoding="utf-8")
    blocks = parse_file(p, "text/plain")
    assert [b["text"] for b in blocks] == ["第一段。", "第二段。"]
    assert all(b["page_no"] is None and b["heading_path"] == "" for b in blocks)


def test_pdf_roundtrip(tmp_path):
    import fitz

    p = tmp_path / "a.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF第一页内容。", fontname="china-s")
    doc.new_page().insert_text((72, 72), "PDF第二页内容。", fontname="china-s")
    doc.save(p)
    doc.close()

    blocks = parse_file(p, "application/pdf")
    assert any("PDF第一页内容" in b["text"] and b["page_no"] == 1 for b in blocks)
    assert any("PDF第二页内容" in b["text"] and b["page_no"] == 2 for b in blocks)


def test_docx_roundtrip(tmp_path):
    from docx import Document

    p = tmp_path / "a.docx"
    doc = Document()
    doc.add_heading("指南", level=1)
    doc.add_paragraph("安装说明正文。")
    doc.add_heading("配置", level=2)
    doc.add_paragraph("配置说明正文。")
    doc.save(p)

    blocks = parse_file(p, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert blocks[0]["text"] == "安装说明正文。"
    assert blocks[0]["heading_path"] == "指南"
    assert blocks[1]["text"] == "配置说明正文。"
    assert blocks[1]["heading_path"] == "指南 > 配置"
