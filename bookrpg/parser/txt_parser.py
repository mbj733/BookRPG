"""TXT 解析：读取全文，按段落切块（每块约 3000 字）。

编码自动探测：utf-8-sig → utf-8 → gb18030 → gbk → 兜底 utf-8 ignore。
"""
import re
from pathlib import Path

CHUNK_SIZE = 3000


def parse(path: str) -> list[str]:
    """返回文本块列表（每块不超过约 CHUNK_SIZE 字）。"""
    return chunk_text(_read_text(path))


def chunk_text(text: str) -> list[str]:
    """把整段文本按段落切块。"""
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    cur = ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if cur and len(cur) + len(p) + 1 > CHUNK_SIZE:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    if not chunks:
        raise ValueError("未能提取到任何文本")
    return chunks


def _read_text(path: str) -> str:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")
