"""EPUB 解析（纯标准库：zipfile + 正则）。

- 按 spine 顺序提取正文，忽略封面
- --story 关键词：用 toc.ncx 的章节标签做锚点，把目标篇目从合集中精确切片出来
  （自动跳过目录页/前言里的同名提及；以"下一篇目标签"为结束边界，
   因此中文篇目默认只取到英文标题为止——中英合集按单语篇目建包更干净）
- 返回 (作者, 篇目标签列表, 文本)
"""
import html
import re
import zipfile


def extract(path: str, story_filter: str | None = None) -> tuple[str, list[str], str]:
    """提取 EPUB 文本。

    返回 (author, chapter_labels, text)。
    story_filter 不为空时，只返回匹配篇目的切片文本。
    """
    z = zipfile.ZipFile(path)
    opf = _find_opf(z)
    if not opf:
        raise ValueError("EPUB 缺少 content.opf，无法解析")
    opf_dir = opf.rsplit("/", 1)[0] if "/" in opf else ""
    opf_text = z.read(opf).decode("utf-8", errors="ignore")

    manifest, spine = _parse_opf(opf_text)
    base = f"{opf_dir}/" if opf_dir else ""

    bodies: list[str] = []
    for href in spine:
        full = f"{base}{href}"
        if full not in z.namelist():
            candidates = [n for n in z.namelist() if n.endswith("/" + href)]
            full = candidates[0] if candidates else full
        if full not in z.namelist():
            continue
        raw = z.read(full)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="ignore")
        _, body = _strip_html(text)
        bodies.append(body)

    # 去掉每个章节文件开头的合集页眉（常见于合集 EPUB：<title> 是合集名）
    bodies = [_strip_file_header(b) for b in bodies]

    labels = _parse_toc(z)
    full = "\n\n".join(bodies)

    if story_filter:
        full = _slice_story(full, labels, story_filter)

    author = _get_meta(opf_text)
    return author, labels, full


def list_stories(path: str) -> list[str]:
    """列出 EPUB 全部篇目标签（供 --story 选择）。"""
    z = zipfile.ZipFile(path)
    return _parse_toc(z)


# ---------- 内部 ----------

def _parse_toc(z: zipfile.ZipFile) -> list[str]:
    """从 toc.ncx 解析有序篇目标签（去重保留顺序）。"""
    ncx = None
    for n in z.namelist():
        if n.lower().endswith(("toc.ncx", ".ncx")):
            ncx = n
            break
    if not ncx:
        return []
    raw = z.read(ncx).decode("utf-8", errors="ignore")
    labels: list[str] = []
    for m in re.finditer(r"<text>(.*?)</text>", raw, re.S):
        label = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _slice_story(full_text: str, labels: list[str], keyword: str) -> str:
    """按关键词在 TOC 标签里找锚点，切出该篇正文。

    三个关键处理：
    1. 标签核心化：去掉尾部（1998）年份后缀——正文标题通常不带年份
    2. 锚点候选取"到下一篇目标签距离最长"的出现处——正文篇目标题后面跟着
       几万字正文，而目录页/前言/访谈里的同名提及后面只有几十到几千字
    3. 结束边界：锚点后 200 字内的标签是本故事自己的英文标题对（跳过），
       取后续标签中最早出现的位置
    """
    kw = keyword.strip()
    # 1) 找匹配的标签（精确包含优先，其次忽略空白）
    matched_label = None
    for lb in labels:
        if kw in lb:
            matched_label = lb
            break
    if matched_label is None:
        for lb in labels:
            if kw.replace(" ", "") in lb.replace(" ", ""):
                matched_label = lb
                break
    if matched_label is None:
        raise ValueError(f"合集里找不到包含「{kw}」的篇目。可用 `list_stories` 查看全部篇目。")

    anchor_core = _label_core(matched_label)
    after_cores = [_label_core(lb) for lb in labels[labels.index(matched_label) + 1:]]

    def find_occurrences(text: str, label: str) -> list[int]:
        pattern = r"\s*".join(re.escape(c) for c in label)
        return [m.start() for m in re.finditer(pattern, text)]

    def next_boundary(start: int) -> int | None:
        """start 之后第一个"非本故事标题对"的篇目标签位置。"""
        boundaries = []
        for core in after_cores:
            for pos in find_occurrences(full_text, core):
                if pos > start + 200:
                    boundaries.append(pos)
                    break
        return min(boundaries) if boundaries else None

    # 2) 锚点：所有出现处中，到下一篇目标签距离最大者
    candidates = find_occurrences(full_text, anchor_core)
    if not candidates:
        raise ValueError(f"篇目「{matched_label}」的标题在正文中未找到，无法切片。")
    best_start, best_dist = None, -1
    for pos in candidates:
        b = next_boundary(pos)
        dist = (b - pos) if b is not None else (len(full_text) - pos)
        if dist > best_dist:
            best_start, best_dist = pos, dist

    end = next_boundary(best_start) or len(full_text)
    return full_text[best_start:end].strip()


def _strip_file_header(body: str) -> str:
    """去掉章节文件开头的合集页眉行（如"Ted Chiang作品集"）。"""
    lines = body.split("\n")
    while lines and len(lines[0].strip()) <= 20:
        # 只删掉以"作品集/全集/文集/合集"结尾的页眉行
        if re.search(r"(作品集|全集|文集|合集)\s*$", lines[0].strip()):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _label_core(label: str) -> str:
    """标签核心：去掉尾部（1998）/ (1991) 年份后缀（正文标题通常不带）。"""
    return re.sub(r"[（(]\s*\d{4}\s*[）)]\s*$", "", label).strip()


def _find_opf(z: zipfile.ZipFile) -> str | None:
    """找到 content.opf（META-INF/container.xml 指向它）。"""
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", errors="ignore")
        m = re.search(r'full-path="([^"]+\.opf)"', container)
        if m:
            return m.group(1)
    except KeyError:
        pass
    for n in z.namelist():
        if n.lower().endswith(".opf"):
            return n
    return None


def _parse_opf(opf_text: str) -> tuple[dict[str, str], list[str]]:
    """解析 manifest（id→href）与 spine（有序 href 列表）。"""
    manifest: dict[str, str] = {}
    for m in re.finditer(r"<item\b[^>]*>", opf_text):
        tag = m.group(0)
        i = re.search(r'id="([^"]+)"', tag)
        h = re.search(r'href="([^"]+)"', tag)
        media = re.search(r'media-type="([^"]+)"', tag)
        if i and h and media and "html" in media.group(1).lower():
            manifest[i.group(1)] = h.group(1)
    spine: list[str] = []
    for m in re.finditer(r"<itemref\b[^>]*>", opf_text):
        r = re.search(r'idref="([^"]+)"', m.group(0))
        if r and r.group(1) in manifest:
            spine.append(manifest[r.group(1)])
    return manifest, spine


def _get_meta(opf_text: str) -> str:
    """从 OPF metadata 提取作者（dc:creator），找不到返回"未知"。"""
    m = re.search(r"<dc:creator[^>]*>(.*?)</dc:creator>", opf_text, re.S | re.I)
    if m:
        author = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        return author or "未知"
    return "未知"


def _strip_html(raw_text: str) -> tuple[str, str]:
    """返回 (标题, 正文纯文本)。"""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.S | re.I)
    title = html.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip() if title_m else ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_text, flags=re.S | re.I)
    h = re.search(r"<h[12][^>]*>(.*?)</h[12]>", text, re.S | re.I)
    if h:
        title = html.unescape(re.sub(r"<[^>]+>", "", h.group(1))).strip()
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return title, text.strip()
