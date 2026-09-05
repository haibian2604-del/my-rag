"""FTS 分词：jieba 切词后空格 join，写入与查询两侧共用。"""
import jieba


def tokenize_for_fts(text: str) -> str:
    """中文分词为空格分隔串（过滤空白 token），供 to_tsvector('simple', ...) 使用。"""
    return " ".join(t.strip() for t in jieba.lcut(text) if t.strip())
