"""离线逻辑测试：状态系统。"""
import unittest

from bookrpg.state import GameState


class TestGameState(unittest.TestCase):
    def test_default_init(self):
        s = GameState()
        self.assertEqual(s.to_dict(), {"生命": 100, "金钱": 100})

    def test_custom_init(self):
        s = GameState({"生命": 50, "魔法": 30})
        self.assertEqual(s.to_dict(), {"生命": 50, "魔法": 30})

    def test_numeric_add(self):
        s = GameState({"生命": 100})
        s.apply({"生命": -10})
        s.apply({"生命": 5})
        self.assertEqual(s.to_dict()["生命"], 95)

    def test_float_rounding(self):
        s = GameState({"体力": 10})
        s.apply({"体力": -3.333})
        self.assertEqual(s.to_dict()["体力"], 6.67)

    def test_string_overwrite(self):
        s = GameState({"生命": 100})
        s.apply({"生命": "重伤"})
        self.assertEqual(s.to_dict()["生命"], "重伤")
        # 字符串之后数值相加应退化为覆盖
        s.apply({"生命": 5})
        self.assertEqual(s.to_dict()["生命"], 5)

    def test_new_attribute_created(self):
        s = GameState()
        s.apply({"好感_张三": 5})
        self.assertEqual(s.to_dict()["好感_张三"], 5)

    def test_none_and_empty(self):
        s = GameState({"生命": 100})
        s.apply(None)
        s.apply({})
        self.assertEqual(s.to_dict(), {"生命": 100})

    def test_to_dict_returns_copy(self):
        s = GameState()
        d = s.to_dict()
        d["生命"] = 999
        self.assertEqual(s.to_dict()["生命"], 100)


if __name__ == "__main__":
    unittest.main()
