"""离线逻辑测试：通读断点续跑（fake chat，不调 API）。

覆盖：精读成功写缓存 → 聚合中断 → 重导跳过已精读块（续跑）；
部分命中补精读；截断兜底不固化缓存；无 cache_dir 时不产生缓存。
"""
import json
import tempfile
import unittest
from pathlib import Path

from bookrpg import llm, worldbook as wb


def make_book_txt(tmp: Path, name: str = "测试书.txt", n_chunks: int = 3) -> Path:
    """n_chunks 个独立段落（每段 ~1500 字），chunk_text 会切成 n_chunks 块。"""
    para = "雪落无声，山道蜿蜒，密林深处传来隐约的钟鸣。" * 90  # ~1500 字
    p = tmp / name
    p.write_text("\n\n".join(para for _ in range(n_chunks)), encoding="utf-8")
    return p


def is_summarize_call(messages) -> bool:
    """精读调用（SUMMARY_PROMPT）vs 聚合调用（SCHEMA_PROMPT）。"""
    return "书籍精读引擎" in messages[0]["content"]


AGG_OK = {"book": {"title": "测试书", "author": "作者"},
          "world": {"setting": "s", "rules": [], "locations": [], "factions": []},
          "characters": [], "plot": {}, "items": [], "lore_notes": ""}


class TestResumeCache(unittest.TestCase):
    def test_interrupted_then_resume(self):
        """精读写缓存 → 聚合中断 → 重导精读 0 次、聚合成功、缓存清理。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = make_book_txt(tmp)
            cache_dir = tmp / "cache"
            cache_file = cache_dir / "测试书.jsonl"

            # 第一轮：精读成功（写缓存），聚合抛错 = 中断
            calls1 = {"sum": 0, "agg": 0}
            def chat_json1(messages, **kw):
                if is_summarize_call(messages):
                    calls1["sum"] += 1
                    return {"summary": f"摘要{calls1['sum']}"}
                calls1["agg"] += 1
                raise RuntimeError("聚合阶段中断（模拟）")
            orig = llm.chat_json
            llm.chat_json = chat_json1
            try:
                with self.assertRaises(RuntimeError):
                    wb.build_worldbook(str(src), cache_dir=cache_dir)
            finally:
                llm.chat_json = orig
            self.assertEqual(calls1["sum"], 3)
            self.assertGreaterEqual(calls1["agg"], 1)
            self.assertTrue(cache_file.exists())
            self.assertEqual(len(cache_file.read_text(encoding="utf-8").splitlines()), 3)

            # 第二轮：缓存全命中 → 精读 0 次，聚合成功，缓存清理
            calls2 = {"sum": 0, "agg": 0}
            def chat_json2(messages, **kw):
                if is_summarize_call(messages):
                    calls2["sum"] += 1
                    return {"summary": "x"}
                calls2["agg"] += 1
                return dict(AGG_OK)
            llm.chat_json = chat_json2
            try:
                book = wb.build_worldbook(str(src), cache_dir=cache_dir)
            finally:
                llm.chat_json = orig
            self.assertEqual(calls2["sum"], 0)      # 已精读块全部跳过
            self.assertGreaterEqual(calls2["agg"], 1)
            self.assertEqual(book["book"]["title"], "测试书")
            self.assertFalse(cache_file.exists())   # 成功 → 缓存清理

    def test_partial_cache_resumes_remaining(self):
        """部分命中（2/3 块）：只精读缺失的 1 块，已缓存块跳过。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = make_book_txt(tmp)
            cache_dir = tmp / "cache"
            cache_file = cache_dir / "测试书.jsonl"
            cache_dir.mkdir()
            # 手动造"中断残留"：只缓存了第 0、2 块（第 1 块缺失）
            cache_file.write_text(
                json.dumps({"index": 0, "summary": "旧摘要0"}, ensure_ascii=False) + "\n"
                + json.dumps({"index": 2, "summary": "旧摘要2"}, ensure_ascii=False) + "\n",
                encoding="utf-8")

            calls = {"sum": 0, "indexes": []}
            def chat_json(messages, **kw):
                if is_summarize_call(messages):
                    calls["sum"] += 1
                    calls["indexes"].append(messages[1]["content"][:20])
                    return {"summary": "新摘要"}
                return dict(AGG_OK)
            orig = llm.chat_json
            llm.chat_json = chat_json
            try:
                book = wb.build_worldbook(str(src), cache_dir=cache_dir)
            finally:
                llm.chat_json = orig
            self.assertEqual(calls["sum"], 1)       # 只精读缺失的 1 块
            self.assertIn("第2块", calls["indexes"][0])  # 缺失的是 1 基第 2 块
            self.assertFalse(cache_file.exists())

    def test_degraded_chunk_not_cached(self):
        """截断兜底（模型连续不配合）不写缓存：重导时该块会重新精读。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = make_book_txt(tmp)
            cache_dir = tmp / "cache"
            cache_file = cache_dir / "测试书.jsonl"

            state = {"phase": "sum"}
            def chat_json(messages, **kw):
                if not is_summarize_call(messages):
                    return dict(AGG_OK)
                raise llm.JSONResponseError("模型不配合")
            orig = llm.chat_json
            llm.chat_json = chat_json
            try:
                book = wb.build_worldbook(str(src), cache_dir=cache_dir)
            finally:
                llm.chat_json = orig
            self.assertEqual(book["book"]["title"], "测试书")  # 兜底后仍能生成
            self.assertFalse(cache_file.exists())  # 兜底摘要未固化（也无成功缓存可写）

    def test_no_cache_dir_disabled(self):
        """cache_dir=None（旧调用方式）：不产生缓存文件，行为不变。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = make_book_txt(tmp)
            cache_dir = tmp / "cache"
            calls = {"sum": 0}
            def chat_json(messages, **kw):
                if is_summarize_call(messages):
                    calls["sum"] += 1
                    return {"summary": "s"}
                return dict(AGG_OK)
            orig = llm.chat_json
            llm.chat_json = chat_json
            try:
                wb.build_worldbook(str(src))
            finally:
                llm.chat_json = orig
            self.assertEqual(calls["sum"], 3)
            self.assertFalse(cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
