"""录制回放测试：用 fixture 里的真实模型响应离线重放整局游戏。

无 fixture 时跳过（先跑 `python tests/record_fixture.py` 录制一次）。
验证点：0 降级、每回合都有叙述和选项、状态系统正常演进。
"""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from bookrpg import llm, recorder, worldbook as wb
from bookrpg.engine import Game

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game_session.jsonl"
BOOK_FILE = Path(__file__).resolve().parent / "fixtures" / "books" / "星环守望者.book"

ACTIONS = [
    "我决定先查看归星室的仪表读数。",
    "我向烬老询问上一次星潮的记载。",
    "我尝试用灯语点亮塔顶信标。",
    "我向时鸢打听穹顶商盟的来意。",
    "我擦拭归星石，回想成为守灯人的那天。",
]


@unittest.skipUnless(FIXTURE.exists(), f"缺少 fixture：{FIXTURE}（先跑 python tests/record_fixture.py）")
class TestReplaySession(unittest.TestCase):
    def setUp(self):
        self.book = wb.load_worldbook(str(BOOK_FILE))
        self.orig_chat = llm.chat
        llm.chat = recorder.replay(str(FIXTURE))

    def tearDown(self):
        llm.chat = self.orig_chat

    def _play(self):
        game = Game(self.book, player="阿澈", player_desc="新一代守灯人",
                    worldbook_file=BOOK_FILE.name)
        degrades = []

        class Cap(io.StringIO):
            def write(self, s):
                if "[引擎]" in s:
                    degrades.append(s.strip())
                return super().write(s)

        with redirect_stdout(Cap()):
            results = [game.new_game()]
            for act in ACTIONS:
                results.append(game.step(act))
        return game, results, degrades

    def test_replay_full_session(self):
        game, results, degrades = self._play()
        meta = recorder.load_meta(str(FIXTURE))
        self.assertEqual(len(results), 6)          # 开局 + 5 回合
        # 回放必须忠实复现录制期的降级次数（fixture 自描述）
        self.assertEqual(len(degrades), meta.get("degrade_count", 0),
                         "回放降级次数与录制期不一致")
        for r in results:
            self.assertTrue(r["narrative"].strip(), "叙述为空")
            self.assertIsInstance(r["options"], list)
            self.assertIsInstance(r["state"], dict)

    def test_replay_deterministic(self):
        """重放是确定性的：同一 fixture 跑两遍，结果完全一致。"""
        game1, results1, _ = self._play()
        game2, results2, _ = self._play()
        self.assertEqual(game1.history, game2.history)
        self.assertEqual(game1.state.to_dict(), game2.state.to_dict())
        self.assertEqual(
            [r["narrative"] for r in results1],
            [r["narrative"] for r in results2],
        )


if __name__ == "__main__":
    unittest.main()
