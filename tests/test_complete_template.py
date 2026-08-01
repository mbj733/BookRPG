"""「模板补全」测试：complete_state_template 空维度检测 + LLM 补全 + 清洗兜底（monkeypatch，不调 API）。"""
import unittest

from bookrpg import llm, worldbook as wb


def make_book(attrs=None, reveal=None) -> dict:
    return {
        "world": {"setting": "测试世界"},
        "characters": [{"name": "主角", "role": "主角", "relations": "家传功法修炼者"}],
        "plot": {"outline": "大纲"},
        "items": [{"name": "低级储物袋", "desc": "随身携带"}],
        "state_template": {
            "说明": "测试",
            "属性": attrs or {},
            "待揭示": reveal or {},
        },
    }


class TestCompleteTemplate(unittest.TestCase):
    def test_no_empty_dim_no_call(self):
        """无空维度 → 不调用 LLM。"""
        b = make_book(attrs={"生命": 100, "功法": ["家传功法"]})
        orig = llm.chat_json
        called = []
        llm.chat_json = lambda msgs, **kw: called.append(1) or {}
        try:
            self.assertFalse(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(called, [])

    def test_empty_dim_completed(self):
        """空维度（功法: []）→ 调用 LLM 补全。"""
        b = make_book(attrs={"生命": 100, "功法": []})
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {"属性": {"功法": ["家传功法（黄阶）"]}}
        try:
            self.assertTrue(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(b["state_template"]["属性"]["功法"], ["家传功法（黄阶）"])

    def test_string_result_tolerated(self):
        """模型偶发返回字符串而非数组 → 容错转数组。"""
        b = make_book(attrs={"生命": 100, "功法": []})
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {"属性": {"功法": "家传功法（黄阶）"}}
        try:
            self.assertTrue(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(b["state_template"]["属性"]["功法"], ["家传功法（黄阶）"])

    def test_completion_cleaned_against_reveal(self):
        """补全结果混入后期信息（与待揭示交叉）→ 清洗移除。"""
        b = make_book(
            attrs={"生命": 100, "功法": []},
            reveal={"功法": ["地阶高级功法《天火诀》"]},
        )
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {"属性": {"功法": ["家传功法", "地阶高级功法《天火诀》"]}}
        try:
            wb.complete_state_template(b)
        finally:
            llm.chat_json = orig
        # 家传功法保留；与待揭示重复的地阶功法被清洗（按未拥有处理）
        self.assertEqual(b["state_template"]["属性"]["功法"], ["家传功法"])
        self.assertIn("地阶高级功法《天火诀》", b["state_template"]["待揭示"]["功法"])

    def test_bad_shape_no_change(self):
        """补全返回结构不符 → 不修改。"""
        b = make_book(attrs={"生命": 100, "功法": []})
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {"说明": "结构不对"}
        try:
            self.assertFalse(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(b["state_template"]["属性"]["功法"], [])

    def test_api_failure_no_block(self):
        """补全调用抛 RuntimeError → 返回 False，不阻塞流程。"""
        b = make_book(attrs={"生命": 100, "功法": []})
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: (_ for _ in ()).throw(RuntimeError("断网"))
        try:
            self.assertFalse(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(b["state_template"]["属性"]["功法"], [])

    def test_retry_on_empty_result(self):
        """首次补全为空输出 → 重试一次，第二次成功补全。"""
        b = make_book(attrs={"生命": 100, "功法": []})
        seq = [{"属性": {"功法": []}}, {"属性": {"功法": ["家传功法"]}}]
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: seq.pop(0)
        try:
            self.assertTrue(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(b["state_template"]["属性"]["功法"], ["家传功法"])

    def test_two_empty_results_keeps_unchanged(self):
        """两次补全均为空 → 保持原样（模型认为确无内容，不硬编）。"""
        b = make_book(attrs={"生命": 100, "功法": []})
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {"属性": {"功法": []}}
        try:
            self.assertFalse(wb.complete_state_template(b))
        finally:
            llm.chat_json = orig
        self.assertEqual(b["state_template"]["属性"]["功法"], [])

    def test_empty_attr_dims_detector(self):
        b = make_book(attrs={"生命": 100, "功法": [], "物品": ["戒指"], "关系": {}})
        self.assertEqual(wb._empty_attr_dims(b), ["功法"])


if __name__ == "__main__":
    unittest.main()
