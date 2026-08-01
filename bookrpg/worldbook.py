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
    },
    "待揭示": {
      "关系": {"后期才知晓的人物": "首次在剧情中被提到时才揭示的关系描述"},
      "功法": ["后期才获得/修炼的功法，逐条列出"],
      "资源": ["后期才获得的重要物品/装备，逐条列出"]
    }
  },
  "items": [{"name": "物品", "desc": "描述", "importance": "重要性"}],
  "lore_notes": "其他必须记住的设定细节（伏笔、禁忌、冷知识），尽可能具体"
}

要求：
1. characters 里的 arc 字段尤其重要——必须写清原著里该角色的结局走向，这是玩家偏离剧情时判断"哪里和原著不一样"的依据。
2. 只依据提供的摘要归纳，不要编造书中没有的信息；不确定的写"书中未明确"。
3. 内容要具体（具体人名、地名、事件、设定），不要空泛。
4. state_template 是主角的"角色面板/背包"：属性 6~14 个维度，数值维度（生命/金钱/力量等）2~4 个，其余用列表（功法/资源/物品/装备，逐条）和字典（关系：人物→当前关系）表达，不要套固定模板。
5. 「属性」只列主角在原著故事起点（主角故事开始的那一刻）已经知道且已经拥有的东西——关系只写主角当时已认识且知道对方真实身份的人（隐藏身份如"古族大小姐"不写进关系描述，尚未现身的人物如戒指里的神秘灵魂也不列），功法/资源只列已经实际修炼/拥有的（参考世界观包的角色档案与剧情大纲）；主角在故事起点不知道的，即使第一幕立即揭晓，也不入「属性」。
6. 「属性」在"故事起点已知且拥有"范围内要**完整，不得遗漏**：实际修炼的功法（哪怕低阶平凡如家传功法）、随身物品、已认识的人、所属势力等逐条列出，不要只挑亮点；某维度确实没有内容时不列出该维度（禁止空列表）。
7. 「待揭示」列原著中主角后期才知晓/获得的东西：后期才认识或才知道身份的人物（进"关系"）、后期才获得的功法/物品（进对应列表）。这些条目在剧情中首次被提到/获得时才显示，开局不会出现在状态里。条目文本用书中实际名称（如"《焚决》"），便于剧情文本匹配。
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

    # 模板交叉污染清洗 + 空维度补全：防「属性」混入后期信息（剧透）、防遗漏开局状态
    sanitize_state_template(worldbook)
    complete_state_template(worldbook)

    # .book 内容已完整生成：清理断点缓存（下次导入从零精读）
    if cache_path is not None and cache_path.exists():
        cache_path.unlink()
        print(f"[通读] 断点缓存已清理：{cache_path.name}")
    return worldbook


STATE_TEMPLATE_PROMPT = """你是书籍分析引擎。根据给定书籍的世界观包，为该书主角设计「角色状态面板模板」（像游戏里的角色面板/背包）。
只输出 JSON（不要输出任何其他文字），格式：{"说明": "模板设计思路一句话", "属性": {"生命": 100, "金钱": 50, "境界": "…", "功法": ["…", "…"], "资源": ["…"], "所属势力": "…", "关系": {"人物": "关系"}, "……": "…"}, "待揭示": {"关系": {"后期才知晓的人物": "关系描述"}, "功法": ["后期才获得/修炼的功法"], "资源": ["后期才获得的重要物品"]}}
要求：
- 「属性」6~14 个维度；数值维度（生命/金钱/力量等）2~4 个；其余用列表（功法/秘籍/资源/物品/装备，逐条）和字典（关系：人物→当前关系）表达
- 「属性」以原著故事起点（主角故事开始的那一刻）为准，只列主角当时已经知道且已经拥有的：关系只写主角当时已认识且知道对方真实身份的人（隐藏身份如"古族大小姐"不写进关系描述，未现身的人物如戒指里的神秘灵魂也不列）；功法/物品只列已经实际修炼/拥有的（未获得的如《焚决》不列）。主角在故事起点不知道的，即使第一幕立即揭晓，也不入「属性」
- 「属性」在"故事起点已知且拥有"的范围内要**完整，不得遗漏**：主角实际修炼的功法（哪怕低阶平凡，如家传低级功法）、随身物品、已认识的人、所属势力等逐条列出，不要只挑亮点/高光物品；主角故事起点确实没有任何内容的维度**不要列出该维度（禁止空列表，空列表=遗漏信号）**
- 「待揭示」列原著中主角后期才知晓/获得的东西：后期才认识或才知道真实身份的人物（进"关系"，值为按主角得知时的信息写的关系描述）、后期才获得的功法/物品（进对应列表）；条目文本用书中实际名称（如"《焚决》"、"萧玄"），剧情中首次被提到时才揭示，开局不显示
- 属性名用中文；列表项要具体（如"玄阶功法《焚决》"，不要"功法若干"）"""

REVEAL_PROMPT = """你是书籍分析引擎。下面是一本书已有的「角色状态面板模板」与世界观包，请为它补充「待揭示」字段。
「待揭示」列原著中主角后期才知晓/获得、而开局状态面板里不该有的东西：
- "关系"：后期才认识、或后期才知道真实身份的人物（如主角的先祖、后期才出场的师父/宿敌、隐藏身份的人物），值为该人物的关系描述（按主角后期得知时的信息写，如"萧玄": "萧家先祖，千年前斗帝血脉觉醒者"）
- 列表类（"功法"/"资源"/"物品"/"装备"等）：后期才获得/修炼的功法与重要物品（如"《焚决》"）
只输出 JSON：{"待揭示": {...}}，不要输出任何其他文字。条目文本必须用书中实际名称（人物用全名、功法用《书名号》），以便剧情文本匹配。不要把开局已知/已拥有的东西放进「待揭示」。"""


def _name_keys(name: str) -> list[str]:
    """人物名的匹配键：全名 + 括号内别名/常用名（"药老（药尘）"→"药老"、"药尘"）。"""
    keys = [name]
    keys += [p for p in re.split(r"[（）()]", name) if p and p != name]
    return keys


def sanitize_state_template(book: dict) -> bool:
    """清洗 state_template，防止「属性」（开局已知）与「待揭示」（后期信息）交叉污染。

    规则（生成/增强模板后自动执行，不依赖模型自觉）：
    1. 属性.关系 的人物若与待揭示.关系 同名（含括号别名）→ 删**待揭示侧**
       （人物开局已认识，"身份升级"由剧情中 state_changes 更新关系值承担，
       不占揭示槽——否则揭示时会出现两个同人卡片，如萧薰儿 vs 萧薰儿（古薰儿））
    2. 属性各列表条目若与待揭示列表条目相同（含《书名号》内名）→ 删**属性侧**
       （矛盾时按"未拥有"处理：显示未拥有会剧透，已拥有但暂不显示可由剧情揭示补回，
       如《焚决》）
    3. 属性的字符串值（关系描述/列表项）包含待揭示人物名 → 打印警告（不自动删，
       避免误伤描述级内容）

    返回是否发生修改。
    """
    st = book.get("state_template")
    if not isinstance(st, dict):
        return False
    attrs = st.get("属性")
    reveal = st.get("待揭示")
    if not isinstance(attrs, dict) or not isinstance(reveal, dict):
        return False
    changed = False

    reveal_names: set[str] = set()
    for name in (reveal.get("关系") or {}):
        reveal_names.update(_name_keys(name))
    reveal_items: set[str] = set()
    for key, items in reveal.items():
        if key == "关系" or not isinstance(items, list):
            continue
        for it in items:
            reveal_items.add(it)
            reveal_items.update(re.findall(r"《(.+?)》", it))

    # 1. 属性.关系 已有人物 → 待揭示同名条目删除（身份升级走 state_changes）
    rel = attrs.get("关系")
    if isinstance(rel, dict) and isinstance(reveal.get("关系"), dict):
        attr_names = set()
        for name in rel:
            attr_names.update(_name_keys(name))
        for name in list(reveal["关系"]):
            if any(k in attr_names for k in _name_keys(name)):
                del reveal["关系"][name]
                changed = True
                print(f"[模板清洗] 待揭示.关系 移除开局已认识的人物：{name}")

    # 2. 属性列表条目与待揭示列表条目相同 → 删属性侧（按未拥有处理）
    for key, items in attrs.items():
        if key == "关系" or not isinstance(items, list):
            continue
        kept = []
        for it in items:
            it_keys = {it} | set(re.findall(r"《(.+?)》", it))
            if it_keys & reveal_items:
                print(f"[模板清洗] 属性.{key} 移除与待揭示重复的条目（按未拥有处理）：{it}")
                changed = True
            else:
                kept.append(it)
        attrs[key] = kept

    # 3. 描述级污染警告
    for key, items in attrs.items():
        if key == "关系":
            for name, desc in items.items():
                for rn in sorted(reveal_names - set(_name_keys(name))):
                    if rn and rn in str(desc):
                        print(f"[模板清洗] 警告：属性.关系[{name}] 描述含待揭示人物「{rn}」：{desc}")
        elif isinstance(items, list):
            for it in items:
                for rn in sorted(reveal_names):
                    if rn and rn in str(it):
                        print(f"[模板清洗] 警告：属性.{key} 条目含待揭示人物「{rn}」：{it}")
    return changed


def _empty_attr_dims(book: dict) -> list[str]:
    """检测属性中的空列表维度（模型遗漏信号）。空列表=该维度应有内容却未列出。"""
    st = book.get("state_template") or {}
    attrs = st.get("属性") or {}
    return [k for k, v in attrs.items() if isinstance(v, list) and not v]


COMPLETE_PROMPT = """你是书籍分析引擎。下面是一本书已生成的「角色状态面板模板」，其中某些维度是空列表——说明模板遗漏了主角在故事起点实际拥有的内容。
请根据世界观包资料（角色档案/剧情大纲/物品/世界观设定），为这些空维度补充内容：
- 只补主角在原著故事起点（主角故事开始的那一刻）已经知道且已经拥有的东西
- 平凡低阶的也算（如主角修炼的家传低级功法、随身杂物、日常物品），不要只挑高光/名场面
- 严格禁止：后期才获得/知晓的东西（未获得的功法、隐藏身份、后期才出场的人物）、编造书中没有的东西
- 拿不准或资料中确实没有的：保持空（不要硬编）
只输出 JSON：{"属性": {"功法": ["..."], ...}}，只包含空维度的补全内容，不要输出任何其他文字。"""


def complete_state_template(book: dict) -> bool:
    """补全模板中遗漏的空维度（模型遗漏兜底）：检测空列表 → LLM 补全 → 交叉污染清洗。

    只在存在空列表维度时调用 LLM；模型偶发空输出/结构漂移时重试一次（共 2 次），
    两次皆空则保持原样（不硬编）。补全内容再过 sanitize_state_template
    （防止补全时把后期信息混入属性）。返回是否发生修改。
    """
    empty = _empty_attr_dims(book)
    if not empty:
        return False
    print(f"[模板补全] 检测到空维度 {empty}，尝试补全遗漏状态…")
    attrs = (book.get("state_template") or {}).get("属性") or {}
    for attempt in range(2):
        try:
            obj = llm.chat_json(
                [
                    {"role": "system", "content": COMPLETE_PROMPT},
                    {"role": "user", "content": json.dumps(
                        {"world": book.get("world", {}), "characters": book.get("characters", []),
                         "plot": book.get("plot", {}), "items": book.get("items", []),
                         "现有模板": book.get("state_template", {})}, ensure_ascii=False)},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
        except RuntimeError as e:
            print(f"[模板补全] 补全调用失败（{e}），保持原样")
            return False
        filled = obj.get("属性")
        if not isinstance(filled, dict):
            if attempt == 0:
                print("[模板补全] 返回结构不符，重试一次…")
                continue
            print("[模板补全] 补全失败（返回结构不符），保持原样")
            return False
        changed = False
        for k, v in filled.items():
            if k not in empty or not v:
                continue
            if isinstance(v, str):  # 容错：模型偶发返回字符串而非数组
                v = [v.strip()] if v.strip() else []
            if isinstance(v, list) and v:
                attrs[k] = v
                print(f"[模板补全] 补全 属性.{k}：{v}")
                changed = True
        if changed:
            sanitize_state_template(book)  # 补全内容再过交叉污染清洗（防后期信息混入）
            return True
        if attempt == 0:
            print("[模板补全] 补全结果为空，重试一次…")
    print("[模板补全] 两次补全均为空，保持原样（模型认为该维度确无内容）")
    return False


def enhance_state_template(book_path: str | Path, force: bool = False) -> dict:
    """给已导入的 .book 补充/完善 state_template（读世界观包 → 生成 → 写回，不用重新通读）。

    - 无模板：完整生成（属性 + 待揭示）
    - 有属性但缺「待揭示」：只补待揭示（属性保持不变）
    - 两者齐全且 force=False：直接返回；force=True：按新版提示词全量重生成
      （旧模板「属性」里可能混入后期才知晓/获得的信息，如《焚决》/隐藏身份，
       force 重生成会按「开局已知才入属性」的新规则重写）

    返回更新后的 worldbook dict。
    """
    p = Path(book_path)
    book = load_worldbook(p)
    st = book.get("state_template")
    if not force and isinstance(st, dict) and isinstance(st.get("属性"), dict) and isinstance(st.get("待揭示"), dict):
        return book  # 模板齐全
    print(f"[模板] 为 {p.stem} {'全量重生成' if force and isinstance(st, dict) else '生成/补充'}角色状态面板模板…")
    obj = llm.chat_json(
        [
            {"role": "system", "content": STATE_TEMPLATE_PROMPT if (not isinstance(st, dict) or force) else REVEAL_PROMPT},
            {"role": "user", "content": json.dumps(
                {"world": book.get("world", {}), "characters": book.get("characters", []),
                 "plot": book.get("plot", {}), "items": book.get("items", []),
                 "已有模板": st if isinstance(st, dict) else None},
                ensure_ascii=False)},
        ],
        temperature=0.3,
        max_tokens=8192,  # reasoning 模型推理+输出总预算，4096 曾吃光返回空内容
    )
    if not isinstance(st, dict) or (force and isinstance(st, dict)):
        attrs = obj.get("属性")
        if isinstance(attrs, dict) and attrs:
            book["state_template"] = {
                "说明": str(obj.get("说明", "")),
                "属性": attrs,
                "待揭示": obj.get("待揭示") if isinstance(obj.get("待揭示"), dict) else {},
            }
            sanitize_state_template(book)  # 防「属性」与「待揭示」交叉污染
            complete_state_template(book)  # 空维度补全：防遗漏开局状态
            p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[模板] 已写入 {p.name}（{len(attrs)} 个状态维度）")
        else:
            print(f"[模板] 生成失败（返回结构不符），保持原样")
    else:
        reveal = obj.get("待揭示")
        if isinstance(reveal, dict):
            st["待揭示"] = reveal
            sanitize_state_template(book)  # 防「属性」与「待揭示」交叉污染
            complete_state_template(book)  # 空维度补全：防遗漏开局状态
            p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[模板] 已补「待揭示」到 {p.name}（{len(reveal)} 组待揭示条目）")
        else:
            print(f"[模板] 补「待揭示」失败（返回结构不符），保持原样")
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
