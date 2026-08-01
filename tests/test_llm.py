"""离线逻辑测试：llm 的 JSON 容错解析与纠正重试（不调 API）。"""
import unittest

from bookrpg import llm


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(llm.extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(llm.extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prose_prefix(self):
        self.assertEqual(llm.extract_json('好的，结果如下：{"a": 1} 完'), {"a": 1})

    def test_prose_suffix(self):
        self.assertEqual(llm.extract_json('prefix {"n": [1, 2]} suffix'), {"n": [1, 2]})

    def test_braces_in_value(self):
        # rfind("}") 应取最后一个，值里的 } 不破坏解析
        self.assertEqual(llm.extract_json('{"a": "含}花括号"}'), {"a": "含}花括号"})

    def test_pure_prose(self):
        self.assertIsNone(llm.extract_json("纯文本没有JSON"))

    def test_empty(self):
        self.assertIsNone(llm.extract_json(""))
        self.assertIsNone(llm.extract_json(None))

    def test_non_dict_json(self):
        # 数组不是对象，返回 None
        self.assertIsNone(llm.extract_json("[1, 2, 3]"))


class TestChatJsonRetry(unittest.TestCase):
    def test_corrective_retry_succeeds(self):
        """首次返回散文 → 追加纠正消息 → 第二次返回合法 JSON → 成功。"""
        calls = {"n": 0}

        def fake_chat(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "这里是散文叙述，没有JSON"
            return '{"narrative": "ok", "options": [], "state_changes": {}, "scene": "s", "game_over": null}'

        orig = llm.chat
        llm.chat = fake_chat
        try:
            r = llm.chat_json([{"role": "user", "content": "hi"}], retries=2)
        finally:
            llm.chat = orig
        self.assertEqual(r.get("narrative"), "ok")
        self.assertEqual(calls["n"], 2)

    def test_corrective_message_appended(self):
        """连续失败时，重试请求里必须包含纠正消息。"""
        calls = {"n": 0, "msgs": None}

        def fake_chat(messages, **kwargs):
            calls["n"] += 1
            calls["msgs"] = list(messages)
            return "还是散文"

        orig = llm.chat
        llm.chat = fake_chat
        try:
            with self.assertRaises(RuntimeError):
                llm.chat_json([{"role": "user", "content": "x"}], retries=1)
        finally:
            llm.chat = orig
        self.assertEqual(len(calls["msgs"]), 2)  # 原始 + 1 条纠正
        self.assertTrue(any("不是 JSON" in m["content"] for m in calls["msgs"]))

    def test_success_no_extra_calls(self):
        """一次成功不应有多余调用。"""

        def fake_chat(messages, **kwargs):
            return '{"ok": true}'

        orig = llm.chat
        llm.chat = fake_chat
        try:
            r = llm.chat_json([{"role": "user", "content": "y"}], retries=2)
        finally:
            llm.chat = orig
        self.assertEqual(r, {"ok": True})

    def test_exhausted_raises_json_response_error(self):
        """连续非 JSON 用尽重试 → 抛 JSONResponseError（RuntimeError 子类，但可区分 API 故障）。"""

        def fake_chat(messages, **kwargs):
            return "还是散文"

        orig = llm.chat
        llm.chat = fake_chat
        try:
            with self.assertRaises(llm.JSONResponseError):
                llm.chat_json([{"role": "user", "content": "x"}], retries=1)
        finally:
            llm.chat = orig


class TestThinkingAdapters(unittest.TestCase):
    """thinking 快慢适配：按供应商发不同参数（DeepSeek extra_body / OpenAI effort / 通义 enable_thinking）。"""

    def _capture_kwargs(self, mode: str, thinking: bool) -> dict:
        import bookrpg.llm as llm_mod
        seen = {}

        class FakeCompletions:
            def create(self, **kw):
                seen["kw"] = kw
                msg = type("M", (), {"content": "ok"})()
                choice = type("C", (), {"message": msg})()
                return type("R", (), {"choices": [choice]})()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        orig = llm_mod.OpenAI
        llm_mod.OpenAI = lambda **kw: FakeClient()
        try:
            llm.chat([{"role": "user", "content": "hi"}],
                     cfg={"api_key": "k", "base_url": "u", "model": "m",
                          "thinking_mode": mode},
                     thinking=thinking, max_tokens=10)
        finally:
            llm_mod.OpenAI = orig
        return seen["kw"]

    def test_deepseek_fast_disables_thinking(self):
        kw = self._capture_kwargs("deepseek", thinking=False)
        self.assertEqual(kw["extra_body"], {"thinking": {"type": "disabled"}})

    def test_deepseek_deep_enables_thinking(self):
        kw = self._capture_kwargs("deepseek", thinking=True)
        self.assertEqual(kw["extra_body"], {"thinking": {"type": "enabled"}})

    def test_openai_effort_low_high(self):
        kw_fast = self._capture_kwargs("openai_effort", thinking=False)
        self.assertEqual(kw_fast["reasoning_effort"], "low")
        self.assertNotIn("extra_body", kw_fast)  # 不能带 DeepSeek 专属参数
        kw_deep = self._capture_kwargs("openai_effort", thinking=True)
        self.assertEqual(kw_deep["reasoning_effort"], "high")

    def test_qwen_enable_thinking(self):
        kw_fast = self._capture_kwargs("qwen", thinking=False)
        self.assertEqual(kw_fast["extra_body"], {"enable_thinking": False})
        kw_deep = self._capture_kwargs("qwen", thinking=True)
        self.assertEqual(kw_deep["extra_body"], {"enable_thinking": True})

    def test_none_no_extra_params(self):
        kw = self._capture_kwargs("none", thinking=False)
        self.assertNotIn("extra_body", kw)
        self.assertNotIn("reasoning_effort", kw)


class TestListModels(unittest.TestCase):
    def test_list_models_success(self):
        import bookrpg.llm as llm_mod

        class FakeModels:
            def list(self):
                m1 = type("M", (), {"id": "b-model"})()
                m2 = type("M", (), {"id": "a-model"})()
                return type("R", (), {"data": [m1, m2]})()

        class FakeClient:
            models = FakeModels()

        orig = llm_mod.OpenAI
        llm_mod.OpenAI = lambda **kw: FakeClient()
        try:
            out = llm.list_models({"api_key": "k", "base_url": "u"})
        finally:
            llm_mod.OpenAI = orig
        self.assertEqual(out, ["a-model", "b-model"])  # 排序返回

    def test_list_models_failure(self):
        import bookrpg.llm as llm_mod

        class FakeModels:
            def list(self):
                raise RuntimeError("网络错误")

        class FakeClient:
            models = FakeModels()

        orig = llm_mod.OpenAI
        llm_mod.OpenAI = lambda **kw: FakeClient()
        try:
            with self.assertRaisesRegex(RuntimeError, "获取模型列表失败"):
                llm.list_models({"api_key": "k", "base_url": "u"})
        finally:
            llm_mod.OpenAI = orig


if __name__ == "__main__":
    unittest.main()
