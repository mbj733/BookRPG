"""PDF 解析（PyMuPDF/fitz）：逐页提取文本层 → 复用 txt_parser.chunk_text。

- 只支持有文本层的 PDF（电子版导出）；扫描版（纯图片）提取为空，
  会抛出明确错误提示用 OCR 路线
- 轻量清洗：去掉纯数字行（页码）、压缩多余空白
- 返回 (作者, 目录标签列表, 文本)；作者来自 PDF 元数据，缺失回退"未知"
"""
import re


def extract(path: str, story_filter: str | None = None) -> tuple[str, list[str], str]:
    """提取 PDF 文本。返回 (author, labels, text)。

    story_filter 忽略（PDF 一般是单本，不做篇目切片；与 EPUB 签名保持一致）。
    """
    import fitz  # PyMuPDF 延迟导入：仅 PDF 导入路径加载

    doc = fitz.open(path)
    try:
        meta = doc.metadata or {}
        author = (meta.get("author") or "").strip() or "未知"
        # 书签大纲（level, title, page）→ 只取标题列表（供 --story 等展示）
        labels = [t for _, t, _ in doc.get_toc()]
        pages: list[str] = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(_clean_page(text))
    finally:
        doc.close()

    full = "\n\n".join(p for p in pages if p).strip()
    if not full:
        raise ValueError("PDF 未提取到任何文本：可能是扫描版（无文本层）。"
                         "请使用 TXT/EPUB，或先对 PDF 做 OCR 再导入。")
    return author, labels, full


def _clean_page(text: str) -> str:
    """轻量清洗：去掉纯数字行（页码），压缩空白，保留段落结构。"""
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.fullmatch(r"\d{1,4}", s):  # 独立页码行
            continue
        lines.append(s)
    return "\n".join(lines)
