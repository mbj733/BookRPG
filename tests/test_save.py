"""离线逻辑测试：存档/读档往返（假 worldbook，不调 API）。"""
import json
import tempfile
import unittest
from pathlib import Path

from bookrpg.engine import Game
from bookrpg.save import load_game, save_game


def make_worldbook_file(tmp: Path, name: str = "测试书") -> Path:
    wb = {"book": {"title": name, "author": "作者"},
          "world": {"setting": "s"}, "characters": [], "plot": {}}
    p = tmp / f"{name}.book"
    p.write_text(json.dumps(wb, ensure_ascii=False), encoding="utf-8")
    return p


class TestSaveLoad(unittest.TestCase):
    def test_roundtrip_with_worldbook(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            wb_path = make_worldbook_file(tmp)
            g = Game(wb_path and json.loads(wb_path.read_text(encoding="utf-8")),
                     player="主角甲", player_desc="测试",
                     worldbook_file=wb_path.name)
            g.history = [{"role": "user", "content": "行动"},
                         {"role": "assistant", "content": "叙述"}]
            g.scene = "某场景"
            g.state.apply({"好感_X": 3})

            sp = tmp / "saves" / "测试书" / "s1.json"
            save_game(g, sp)
            g2 = load_game(sp, tmp)
            self.assertEqual(g2.player, "主角甲")
            self.assertEqual(g2.scene, "某场景")
            self.assertEqual(g2.history, g.history)
            self.assertEqual(g2.state.to_dict(), g.state.to_dict())
            self.assertEqual(g2.worldbook_file, wb_path.name)
            self.assertIsNotNone(g2.worldbook.get("book"))

    def test_roundtrip_missing_worldbook(self):
        """worldbook 文件缺失时也能恢复（基于历史继续），不崩溃。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            g = Game({}, player="P", worldbook_file="不存在的.book")
            g.history = [{"role": "user", "content": "x"},
                         {"role": "assistant", "content": "y"}]
            sp = tmp / "s1.json"
            save_game(g, sp)
            g2 = load_game(sp, tmp)
            self.assertEqual(g2.player, "P")
            self.assertEqual(len(g2.history), 2)
            self.assertEqual(g2.worldbook, {})

    def test_roundtrip_summary(self):
        """长局压缩的 summary 随存档往返。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            wb_path = make_worldbook_file(tmp)
            g = Game(json.loads(wb_path.read_text(encoding="utf-8")), worldbook_file=wb_path.name)
            g.history = [{"role": "user", "content": "行动"},
                         {"role": "assistant", "content": "叙述"}]
            g.summary = "（前情提要：主角完成了新手任务。）"
            sp = tmp / "saves" / "测试书" / "s1.json"
            save_game(g, sp)
            g2 = load_game(sp, tmp)
            self.assertEqual(g2.summary, g.summary)

    def test_old_save_without_summary(self):
        """旧存档无 summary 字段 → 读档正常，summary 为空（不压缩不渲染）。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            wb_path = make_worldbook_file(tmp)
            sp = tmp / "saves" / "测试书" / "old.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps({
                "player": "P", "player_desc": "", "state": {}, "scene": "s",
                "game_over": None, "options": [],
                "history": [{"role": "user", "content": "x"},
                            {"role": "assistant", "content": "y"}],
                "worldbook_file": wb_path.name,
            }, ensure_ascii=False), encoding="utf-8")
            g2 = load_game(sp, tmp)
            self.assertEqual(g2.summary, "")
            self.assertEqual(len(g2.history), 2)


if __name__ == "__main__":
    unittest.main()
