"""世界观包生成与加载。

流程（多轮通读）：
1. 书籍分块（TXT/EPUB 自适应，~3000字/块）
2. 逐块精读 → 每块 150 字摘要（单块失败单独重试，不重跑全书）
3. 全部摘要汇总 → LLM 按固定 schema 输出完整世界观包 JSON
4. 写入 books/{书名}.book（含元信息）

失败兜底：某块精读连续 3 次失败时，用原文截断 150 字作摘要，不阻塞全书。
EPUB 合集可用 story=<篇名关键词> 只通读单篇。
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import llm
from .parser import epub_parser, pdf_parser, txt_parser

SCHEMA_PROMPT = """你是一个严谨的书籍分析引擎。请根据给定的书籍内容摘要，输出该书的完整「世界观包」JSON。

必须严格输出 JSON（不要输出任何其他文字），schema 如下：
{
  "book": {"title": "书名", "author": "作者"},
  "world": {
    "setting": "时代背景、世界格局一句话总览",
    "rules": ["世界运行规则、力量体系、社会规则，逐条"],
    "locations": [{"name": "地名", "desc": "描述", "significance": "在剧情中的作用"}],
    "factions": [{"name": "势力名", "goal": "目标", "relations": "与各方关系"}]
  },
  "characters": [
    {"name": "角色名", "role": "主角/配角/反派", "personality": "性格与行为模式",
     "motivation": "动机", "relations": "与关键角色的关系", "arc": "人物弧线/原著结局走向"}
  ],
  "plot": {"outline": "主线剧情分幕大纲（起承转合）", "key_events": ["关键事件，按时间顺序"],
           "themes": "主题思想", "original_ending": "原著结局简述"},
  "state_template": {
    "说明": "为本书主角设计的初始状态面板思路（一句话：应该从哪些维度体现主角的状态）",
    "属性": {
      "生命": 100,
      "金钱": 50,
      "境界": "境界/实力（字符串）",
      "功法": ["拥有的功法/秘籍，逐条列出"],
      "资源": ["拥有的资源/物品/丹药/装备，逐条列出"],
      "所属势力": "宗门/家族/阵营",
      "关系": {"关键人物": "当前关系（如: 信任/敌对/有恩）"},
      "……": "其他贴合本书的维度（如 声望/任务进度/仇恨值/异火 等）"
    }
  },
  "items": [{"name": "物品", "desc": "描述", "importance": "重要性"}],
  "lore_notes": "其他必须记住的设定细节（伏笔、禁忌、冷知识），尽可能具体"
}

要求：
1. characters 里的 arc 字段尤其重要——必须写清原著里该角色的结局走向，这是玩家偏离剧情时判断"哪里和原著不一样"的依据。
2. 只依据提供的摘要归纳，不要编造书中没有的信息；不确定的写"书中未明确"。
3. 内容要具体（具体人名、地名、事件、设定），不要空泛。
4. state_template 是主角的"角色面板/背包"：属性 6~14 个维度，数值维度（生命/金钱/力量等）2~4 个，其余用列表（功法/资源/物品/装备，逐条）和字典（关系：人物→当前关系）表达，全部来自原著开局时主角真实拥有的东西；不要套固定模板。
"""

SUMMARY_PROMPT = """你是书籍精读引擎。阅读下面的章节片段，输出 150 字左右的摘要 JSON：{"summary": "..."}。
只输出 JSON，不要输出其他任何内容。摘要要包含：发生了什么关键事件、出场的重要角色、透露的设定信息。"""

# 提速参数（依据 DeepSeek 官方文档：v4-flash 账号级并发上限 2500）
BATCH_SIZE = 1      # 每批块数。实测：>1 时模型一次总结多块会丢失细节（角色/结局走样），故默认 1
CONCURRENCY = 16    # 并发批数（单块精读并发，墙钟时间 ÷16，质量与串行完全一致）
GROUP_SIZE = 80     # 摘要超过此数量时启用分层聚合（组内浓缩 → 最终汇总），防上下文超窗

BATCH_SUMMARY_PROMPT = """你是书籍精读引擎。下面给出若干章节片段，请为每个片段输出 150 字左右的摘要。
只输出一个 JSON 对象，不要输出其他任何内容，格式：{"summaries": ["片段1摘要", "片段2摘要", ...]}
摘要数量必须与片段数量一致（严格一一对应）。摘要要包含：发生了什么关键事件、出场的重要角色、透露的设定信息。
片段用【片段1】、【片段2】...标记。"""


GROUP_SUMMARY_PROMPT = """你是书籍分析引擎。下面是一部长篇书籍某一部分的章节摘要列表。请把这一部分浓缩成 600-800 字的总结。
只输出 JSON：{"summary": "..."}，不要输出其他任何内容。
必须保留：新出场的重要角色（写清人名）、重要地点、关键事件、力量体系/规则设定、伏笔。
不要编造摘要里没有的信息。"""


def _aggregate(summaries: list[str], src: Path, title: str, author: str,
               progress=None) -> dict:
    """生成世界观包。短书（摘要数 ≤ GROUP_SIZE）单次聚合；长书两层聚合：
    第一层把摘要分组并发浓缩成"部分总结"，第二层汇总所有部分总结生成世界观包。
    两层都只携带浓缩信息，输入量可控（防 40 万 tokens 超窗），且比单次超大调用更快。
    """
    def finalize(group_texts: list[str]) -> dict:
        time.sleep(2)  # 并发打完后给服务端喘息，降低空响应概率
        if progress:
            progress(0, 1, "最终汇总")
        result = llm.chat_json(
            [
                {"role": "system", "content": SCHEMA_PROMPT},
                {"role": "user", "content":
                    f"以下是全书共 {len(summaries)} 块的摘要，已浓缩为 {len(group_texts)} 部分（按顺序）：\n\n"
                    + "\n---\n".join(f"【第{k}部分】{s}" for k, s in enumerate(group_texts, 1))},
            ],
            temperature=0.1,    # 聚合温度：0.3 时每次生成的变体差异大，压低增强稳定性
            max_tokens=12000,   # thinking=False 时全部为输出预算，大 JSON 完整输出
            thinking=False,     # 归纳整理无需深度推理；True 时推理吃预算且响应慢数倍
        )
        if progress:
            progress(1, 1, "最终汇总")
        return result

    if len(summaries) <= GROUP_SIZE:
        return finalize(["\n".join(f"【第{k}块】{s}" for k, s in enumerate(summaries, 1))])

    print(f"[通读] 摘要 {len(summaries)} 条超限，分层聚合（{len(summaries) // GROUP_SIZE + 1} 组）…")
    groups = [summaries[i:i + GROUP_SIZE] for i in range(0, len(summaries), GROUP_SIZE)]
    group_texts: list[str] = [""] * len(groups)
    lock = threading.Lock()
    done = [0]

    def work(g: int):
        content = (f"这是长篇书籍第 {g + 1}/{len(groups)} 部分的章节摘要（共 {len(groups[g])} 条）：\n\n"
                   + "\n".join(f"【第{k}条】{s}" for k, s in enumerate(groups[g], 1)))
        for attempt in range(2):
            try:
                obj = llm.chat_json(
                    [
                        {"role": "system", "content": GROUP_SUMMARY_PROMPT},
                        {"role": "user", "content": content},
                    ],
                    temperature=0.1,
                    max_tokens=4096,   # reasoning 下 2048 会被推理吃光→空内容重试循环
                    thinking=False,    # 浓缩任务无需深度推理，更快更稳
                )
                s = obj.get("summary")
                if isinstance(s, str) and s.strip():
                    group_texts[g] = s.strip()
                    break
                print(f"  部分{g + 1} 总结为空，重试…")
            except llm.JSONResponseError as e:
                print(f"  部分{g + 1} 失败（第{attempt + 1}次）: {e}")
        else:
            group_texts[g] = content[:600]  # 兜底：截断原摘要
        with lock:
            done[0] += 1
            cur = done[0]
        if progress:
            progress(cur, len(groups), "汇总")
        if cur == len(groups):
            print(f"  汇总进度 {cur}/{len(groups)}")

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        list(ex.map(work, range(len(groups))))
    return finalize(group_texts)


def build_worldbook(source_path: str, story: str | None = None, progress=None,
                    cache_dir: Path | str | None = None) -> dict:
    """通读全书（或 EPUB 单篇）→ 返回完整世界观包 dict（未写入磁盘）。

    cache_dir 非 None 时启用精读断点续跑：每块精读成功立即追加写入
    `{cache_dir}/{书名}.jsonl`；中断后重导先读缓存，已精读块跳过（进度显示
    "续跑"），聚合阶段仍会重跑；最终成功返回前删除缓存文件。
    """
    src = Path(source_path)
    ext = src.suffix.lower()

    if ext == ".epub":
        author, titles, full_text = epub_parser.extract(str(src), story_filter=story)
        title = story if story else src.stem
        print(f"[EPUB] 提取 {len(titles)} 个章节：{titles[:6]}{'…' if len(titles) > 6 else ''}")
        if story:
            print(f"[EPUB] 已筛选单篇「{story}」")
        chunks = txt_parser.chunk_text(full_text)
    elif ext == ".txt":
        author, title, chunks = "未知", src.stem, txt_parser.parse(str(src))
    elif ext == ".pdf":
        author, titles, full_text = pdf_parser.extract(str(src), story_filter=story)
        title = story if story else src.stem
        print(f"[PDF] 提取 {len(titles)} 个目录条目，共 {len(full_text)} 字")
        chunks = txt_parser.chunk_text(full_text)
    else:
        raise ValueError(f"暂不支持 {ext} 格式。支持 TXT / EPUB / PDF。")

    total = len(chunks)
    print(f"[通读] 全书共 {total} 块（每块约3000字），批量精读（每批{BATCH_SIZE}块 × {CONCURRENCY}并发）…")

    # 断点续跑缓存：精读成功即写，中断重导跳过已精读块（键 = 0 基块号）
    cache: dict[int, str] = {}
    cache_path: Path | None = None
    cache_lock = threading.Lock()
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_name = re.sub(r'[\\/:*?"<>|]', "_", story or src.stem) + ".jsonl"
        cache_path = cache_dir / cache_name
        if cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    cache[int(rec["index"])] = str(rec["summary"])
                except Exception:
                    continue  # 坏行忽略，对应块会重新精读
        if cache:
            print(f"[通读] 续跑：从缓存恢复 {len(cache)}/{total} 块（已精读块跳过）")

    def write_cache(idx0: int, summary: str) -> None:
        if cache_path is None:
            return
        with cache_lock:
            with open(cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"index": idx0, "summary": summary},
                                   ensure_ascii=False) + "\n")

    batches = [chunks[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    summaries: list[str] = [""] * total
    done = [0]
    lock = threading.Lock()

    def work(batch_no: int):
        indexed = [(batch_no * BATCH_SIZE + j, c) for j, c in enumerate(batches[batch_no])]
        idx0 = indexed[0][0]
        if BATCH_SIZE == 1:
            if idx0 in cache:
                sums = [cache[idx0]]                       # 缓存命中：跳过精读
                stage = "续跑"
            else:
                sums = [_summarize_chunk(indexed[0][1], idx0 + 1, cache_writer=write_cache)]
                stage = "精读"
        else:
            sums = _summarize_batch(indexed, batch_no + 1)
            stage = "精读"
        with lock:
            for (idx, _), s in zip(indexed, sums):
                summaries[idx] = s
            done[0] += len(sums)
            cur = done[0]
        if progress:
            progress(cur, total, stage)
        if cur % 40 == 0 or cur == total:
            print(f"  精读进度 {cur}/{total}")

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        list(ex.map(work, range(len(batches))))

    print("[通读] 精读完成，汇总生成世界观包…")
    worldbook = _aggregate(summaries, src, title, author, progress=progress)

    # 补齐元信息（书名以源文件名为准，LLM 给的占位值不算数）
    worldbook.setdefault("book", {})
    llm_title = str(worldbook["book"].get("title", "")).strip()
    if not llm_title or llm_title in ("未明确", "书中未明确", "未知", "无"):
        worldbook["book"]["title"] = title
    if not worldbook["book"].get("author") or str(worldbook["book"].get("author")) in ("未明确", "书中未明确", "未知", "无", ""):
        worldbook["book"]["author"] = author
    worldbook["book"]["source"] = str(src)
    worldbook["book"]["created_at"] = datetime.now().isoformat(timespec="seconds")

    # .book 内容已完整生成：清理断点缓存（下次导入从零精读）
    if cache_path is not None and cache_path.exists():
        cache_path.unlink()
        print(f"[通读] 断点缓存已清理：{cache_path.name}")
    return worldbook


STATE_TEMPLATE_PROMPT = """你是书籍分析引擎。根据给定书籍的世界观包，为该书主角设计「角色状态面板模板」（像游戏里的角色面板/背包）。
只输出 JSON（不要输出任何其他文字），格式：{{"说明": "模板设计思路一句话", "属性": {{"生命": 100, "金钱": 50, "境界": "…", "功法": ["…", "…"], "资源": ["…"], "所属势力": "…", "关系": {{"人物": "关系"}}, "……": "…"}}}}
要求：
- 属性 6~14 个维度；数值维度（生命/金钱/力量等）2~4 个；其余用列表（功法/秘籍/资源/物品/装备，逐条）和字典（关系：人物→当前关系）表达
- 全部来自原著开局时主角真实拥有的东西（参考世界观包的角色档案与剧情大纲），不要套固定模板
- 属性名用中文；列表项要具体（如"玄阶功法《焚决》"，不要"功法若干"）"""


def enhance_state_template(book_path: str | Path) -> dict:
    """给已导入的 .book 补充 state_template（读世界观包 → 生成 → 写回，不用重新通读）。

    返回更新后的 worldbook dict。
    """
    p = Path(book_path)
    book = load_worldbook(p)
    if isinstance(book.get("state_template"), dict):
        return book  # 已有模板
    print(f"[模板] 为 {p.stem} 生成角色状态面板模板…")
    obj = llm.chat_json(
        [
            {"role": "system", "content": STATE_TEMPLATE_PROMPT},
            {"role": "user", "content": json.dumps(
                {"world": book.get("world", {}), "characters": book.get("characters", []),
                 "plot": book.get("plot", {}), "items": book.get("items", [])},
                ensure_ascii=False)},
        ],
        temperature=0.3,
        max_tokens=8192,  # reasoning 模型推理+输出总预算，4096 曾吃光返回空内容
    )
    attrs = obj.get("属性")
    if isinstance(attrs, dict) and attrs:
        book["state_template"] = {"说明": str(obj.get("说明", "")), "属性": attrs}
        p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[模板] 已写入 {p.name}（{len(attrs)} 个状态维度）")
    else:
        print(f"[模板] 生成失败（返回结构不符），保持原样")
    return book


def _summarize_batch(batch: list[tuple[int, str]], batch_no: int) -> list[str]:
    """一次调用总结一批片段，返回与 batch 严格对齐的摘要列表。

    失败重试 2 次；仍失败则降级为该批逐块单块精读（保持"不阻塞全书"兜底）。
    """
    labeled = "\n\n".join(f"【片段{idx}】\n{chunk[:3000]}" for idx, chunk in batch)
    for attempt in range(2):
        try:
            obj = llm.chat_json(
                [
                    {"role": "system", "content": BATCH_SUMMARY_PROMPT},
                    {"role": "user", "content": labeled},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            sums = obj.get("summaries")
            if (isinstance(sums, list) and len(sums) == len(batch)
                    and all(isinstance(s, str) and s.strip() for s in sums)):
                return [s.strip() for s in sums]
            n = len(sums) if isinstance(sums, list) else "非列表"
            print(f"  批{batch_no} 摘要数量不符（{n} ≠ {len(batch)}），重试…")
        except llm.JSONResponseError as e:
            print(f"  批{batch_no} 失败（第{attempt + 1}次）: {e}")
    print(f"  批{batch_no} 批量总结失败，降级为逐块精读")
    return [_summarize_chunk(chunk, idx) for idx, chunk in batch]


def _summarize_chunk(chunk: str, index: int, cache_writer=None) -> str:
    """精读单块，失败单独重试 3 次；仍失败则截断原文兜底。

    cache_writer(index0, summary)：精读**成功**时回调（用于断点续跑写缓存；
    截断兜底不写——重导时会重新精读，避免把劣质摘要固化）。"""
    for attempt in range(3):
        try:
            obj = llm.chat_json(
                [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": f"章节片段（第{index}块）：\n{chunk[:3000]}"},
                ],
                temperature=0.3,
                max_tokens=500,
            )
            s = obj.get("summary") or obj
            out = s if isinstance(s, str) else str(s)
            if cache_writer is not None:
                cache_writer(index - 1, out)  # index 为 1 基，缓存键用 0 基
            return out
        except llm.JSONResponseError as e:
            print(f"  第{index}块精读失败（第{attempt + 1}次）: {e}")
            if attempt == 2:
                return chunk[:150]
    return chunk[:150]  # 不可达，仅为类型兜底


def save_worldbook(worldbook: dict, books_dir: Path) -> Path:
    """写入 books/{书名}.book，返回文件路径。"""
    books_dir.mkdir(parents=True, exist_ok=True)
    title = re.sub(r'[\\/:*?"<>|]', "_", str(worldbook["book"].get("title", "未命名")))
    out = books_dir / f"{title}.book"
    out.write_text(json.dumps(worldbook, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_worldbook(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
