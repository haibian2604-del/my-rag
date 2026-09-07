# app/services/ingestion/parsing.py
"""文档解析：将 md/txt/pdf/docx 统一解析为 Block 列表。

Block = {"text": str, "page_no": int | None, "heading_path": str}
"""
import re
from pathlib import Path

import pymupdf  # 新 API（import fitz 已弃用）
from docx import Document as DocxDocument
from markdown_it import MarkdownIt

md_parser = MarkdownIt()

SUPPORTED = {".md", ".txt", ".pdf", ".docx"}

# FAQ 模式标记：markdown 文件首部含此注释即按 FAQ 解析（M5-T4）
FAQ_MARKER = "<!-- faq -->"
FAQ_MARKER_SCAN_CHARS = 500  # 只扫描文件首部这一字符数内的标记

# Q:/A:（兼容全角冒号）；**问**/**答**（冒号可选）
_Q_RE = re.compile(r"^(?:Q|问)\s*[:：]\s*(.*)$", re.IGNORECASE)
_A_RE = re.compile(r"^(?:A|答)\s*[:：]\s*(.*)$", re.IGNORECASE)
_Q_BOLD_RE = re.compile(r"^\*\*\s*问\s*\*\*\s*[:：]?\s*(.*)$")
_A_BOLD_RE = re.compile(r"^\*\*\s*答\s*\*\*\s*[:：]?\s*(.*)$")


def _match_q(line: str) -> str | None:
    """Q 行匹配：返回问题文本（含同行剩余部分），不匹配返回 None。"""
    for pat in (_Q_BOLD_RE, _Q_RE):
        m = pat.match(line)
        if m:
            return m.group(1).strip()
    return None


def _match_a(line: str) -> str | None:
    for pat in (_A_BOLD_RE, _A_RE):
        m = pat.match(line)
        if m:
            return m.group(1).strip()
    return None


def _faq_block(question: str, answer: str) -> dict:
    """一个 FAQ 对 = 一个 Block：父块全文 = 问题+答案合并，heading_path 即 FAQ 标题；
    faq_question 键供切分层把 question 部分单独作为子块。"""
    question = question.strip()
    answer = answer.strip()
    return {
        "text": f"{question}\n{answer}",
        "page_no": None,
        "heading_path": f"FAQ: {question[:30]}",
        "faq_question": question,
    }


def _parse_table_row(line: str) -> list[str] | None:
    """解析 markdown 表格行为单元格列表；非表格行返回 None。"""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return None
    cells = [c.strip() for c in s[1:-1].split("|")]
    if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):  # 分隔行
        return []
    return cells


def _parse_faq_text(content: str) -> list[dict]:
    """从 markdown 文本解析 FAQ 对，支持三种形态：

    - ``Q:``/``A:`` 行对（兼容 ``**问**``/``**答**`` 与中文冒号）；
    - 两列 markdown 表格（| 问题 | 答案 |），跳过表头与分隔行；
    - Q 行后的普通行并入问题、A 行后的普通行并入答案（多行问答）。
    解析不出任何 FAQ 对时返回空列表（调用方降级为普通解析）。
    """
    pairs: list[dict] = []
    cur: dict | None = None  # {"question": ..., "answer": ...}
    in_table = False

    def flush() -> None:
        nonlocal cur
        if cur and cur["question"]:
            pairs.append(_faq_block(cur["question"], cur["answer"]))
        cur = None

    for line in content.splitlines():
        row = _parse_table_row(line)
        if row is not None:  # 表格行：两列取 (问题, 答案)，其余列忽略
            in_table = True
            flush()
            if len(row) >= 2 and row[0] and row[1]:
                # 跳过表头（问题/答案/Question/Answer）
                if re.fullmatch(r"(?i)问题|答案|question|answer", row[0]) or \
                        re.fullmatch(r"(?i)问题|答案|question|answer", row[1]):
                    continue
                pairs.append(_faq_block(row[0], row[1]))
            continue
        if in_table and not line.strip():
            in_table = False  # 空行退出表格态
            continue
        if in_table:
            continue  # 表格紧邻的非空行不并入问答，避免误收

        q = _match_q(line)
        a = _match_a(line)
        if q is not None:
            flush()
            cur = {"question": q, "answer": ""}
        elif a is not None:
            if cur is None:  # 有答案无问题：无法配对，丢弃
                continue
            cur["answer"] = (cur["answer"] + "\n" + a).strip()
        elif cur is not None and line.strip():
            # 问答对内的续行：答案已开始则并入答案，否则并入问题
            if cur["answer"]:
                cur["answer"] += "\n" + line.strip()
            else:
                cur["question"] += "\n" + line.strip()
    flush()
    return pairs


def parse_file(path: Path, mime: str) -> list[dict]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的文件类型: {ext}")
    if ext == ".md":
        head = path.read_text(encoding="utf-8")[:FAQ_MARKER_SCAN_CHARS]
        if FAQ_MARKER in head:
            # FAQ 模式：解析不出任何 Q/A 对时降级为普通 markdown 解析
            faq_blocks = _parse_faq_text(path.read_text(encoding="utf-8"))
            if faq_blocks:
                return faq_blocks
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


SCAN_ERROR = "疑似扫描件 PDF，暂不支持 OCR，请使用含文本层的 PDF"


def _table_to_markdown(rows: list[list[str | None]]) -> str | None:
    """把 find_tables 抽到的单元格矩阵转为 Markdown 管道表（| a | b | + 分隔行）。

    - 至少 2 行 2 列才视为有效表格，避免把画在矩形框里的普通文本误判为表；
    - 单元格内换行折为空格，竖线转义，短行补空单元格。
    """
    if len(rows) < 2 or max(len(r) for r in rows) < 2:
        return None
    n_cols = max(len(r) for r in rows)

    def clean(cell: str | None) -> str:
        return (cell or "").replace("\n", " ").replace("|", "\\|").strip()

    table = [[clean(c) for c in r] + [""] * (n_cols - len(r)) for r in rows]
    lines = ["| " + " | ".join(r) + " |" for r in table]
    # 分隔行插在表头之后
    lines.insert(1, "| " + " | ".join(["---"] * n_cols) + " |")
    return "\n".join(lines)


def _parse_pdf(path: Path) -> list[dict]:
    blocks: list[dict] = []
    with pymupdf.open(path) as doc:
        total_chars = 0
        for page_no, page in enumerate(doc, start=1):
            # 页面条目 = [(y, x, 文本)]，按 (y, x) 排序即先上后下、同行先左后右
            items: list[tuple[float, float, str]] = []
            # 表格抽取：转 Markdown 管道表并按其页面位置插入，同时记录区域
            table_rects: list[pymupdf.Rect] = []
            try:
                finder = page.find_tables()
            except Exception:  # 个别异常页面表格检测失败时降级为纯文本，不阻塞摄取
                finder = None
            if finder is not None:
                for tab in finder.tables:
                    md = _table_to_markdown(tab.extract())
                    if md is None:
                        continue
                    x0, y0, x1, y1 = tab.bbox
                    items.append((y0, x0, md))
                    table_rects.append(pymupdf.Rect(x0, y0, x1, y1))
            # 普通文本块：块级 (y, x) 排序保证双栏可读；与表格区域重叠过半的
            # 块（即表格单元格文字）剔除，避免表格内容重复提取
            for b in page.get_text("blocks", sort=True):
                if b[6] != 0:  # 1 = 图片块
                    continue
                rect = pymupdf.Rect(b[:4])
                if any(
                    not (rect & tr).is_empty and (rect & tr).get_area() > 0.5 * rect.get_area()
                    for tr in table_rects
                ):
                    continue
                for text in _split_paragraphs(b[4]):
                    items.append((b[1], b[0], text))
            items.sort(key=lambda it: (it[0], it[1]))
            for _, _, text in items:
                blocks.append({"text": text, "page_no": page_no, "heading_path": ""})
            total_chars += len(page.get_text())
    # 扫描件识别：全文几乎没有可提取文本层时明确报错，避免产出空文档
    if total_chars < 20:
        raise ValueError(SCAN_ERROR)
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
