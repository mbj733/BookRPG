"""录制回放：把真实 LLM 调用记录到 JSONL，测试时离线重放。

用法：
    from bookrpg import recorder, llm
    # 录制（一次性，烧真实 token）：
    llm.chat = recorder.record(llm.chat, "tests/fixtures/game_session.jsonl")
    game.new_game(); game.step(...)  # 正常跑，每次调用自动落盘

    # 回放（离线、零成本、确定性、可重复）：
    llm.chat = recorder.replay("tests/fixtures/game_session.jsonl")
    game.new_game(); game.step(...)  # 从记录取响应，不再调 API

匹配策略：指纹 = 全消息的 JSON 哈希（system 含状态与历史，每回合唯一）。
同指纹必返回同响应——重放天然确定、可反复播放。
录制文件末尾可附一条 {"_meta": {...}} 自描述（被回放忽略）。
"""
import hashlib
import json
from pathlib import Path


def fingerprint(messages: list[dict]) -> str:
    """指纹：全消息的规范化 JSON 哈希。"""
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def load_records(path: str) -> list[dict]:
    """读取 JSONL 录制文件（跳过 _meta 行）。"""
    p = Path(path)
    recs = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "_meta" not in rec:
                recs.append(rec)
    return recs


def load_meta(path: str) -> dict:
    """读取录制文件末尾的 _meta 自描述。"""
    p = Path(path)
    if p.exists():
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "_meta" in rec:
                return rec["_meta"]
    return {}


def record(chat_fn, path: str):
    """包装真实 chat：每次调用记录 (messages, response) 到 JSONL，然后正常返回。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def wrapped(messages, **kwargs):
        resp = chat_fn(messages, **kwargs)
        line = {"fp": fingerprint(messages), "messages": messages, "response": resp}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return resp

    return wrapped


def write_meta(path: str, meta: dict) -> None:
    """追加自描述行 {"_meta": {...}} 到录制文件末尾。"""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")


def replay(path: str):
    """返回回放版 chat：按指纹匹配记录，离线返回真实响应（可重复播放）。"""
    recs = load_records(path)
    by_fp: dict[str, dict] = {}
    for r in recs:
        by_fp.setdefault(fingerprint(r["messages"]), r)  # 重算指纹，不信任存储值
    total = len(recs)

    def wrapped(messages, **kwargs):
        fp = fingerprint(messages)
        hit = by_fp.get(fp)
        if hit is not None:
            return hit["response"]
        raise RuntimeError(
            f"回放未命中（fp={fp[:12]}…）。提示词/流程可能已变更，"
            f"或录制不完整（共 {total} 条记录）。请重新录制 fixture。"
        )

    return wrapped
