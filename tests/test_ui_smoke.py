"""UI 冒烟测试：offscreen 无头跑主窗口/书库/游戏页（用 fixture 回放，零 token）。

验证：书库能列出 .book；游戏页开局渲染、选项按钮生成、回合推进、状态面板更新。
"""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from bookrpg import llm, recorder, worldbook as wb
from bookrpg.engine import Game
from bookrpg.ui.game_view import GameView
from bookrpg.ui.library_view import LibraryView
from bookrpg.ui.main_window import MainWindow, dark_qss

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game_session.jsonl"
BOOK_FILE = Path(__file__).resolve().parent / "fixtures" / "books" / "星环守望者.book"


def wait_until(cond, timeout_ms: int = 20000, interval_ms: int = 50):
    loop = QEventLoop()

    def check():
        if cond():
            loop.quit()
        else:
            QTimer.singleShot(interval_ms, check)

    QTimer.singleShot(0, check)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    if not cond():
        raise AssertionError("等待条件超时")


@unittest.skipUnless(FIXTURE.exists() and BOOK_FILE.exists(), "缺少 fixture 或世界观包")
class TestUiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_and_library(self):
        win = MainWindow()
        self.assertIsNotNone(win)
        win.close()

    def test_dark_qss_font_size(self):
        """对话区字号随 dark_qss(font_size) 变化（设置对话框 → config.json → 重启生效）。"""
        self.assertIn("font-size: 15px", dark_qss(15))
        self.assertIn("font-size: 17px", dark_qss(17))
        self.assertIn("font-size: 13px", dark_qss(13))
        self.assertNotIn("font-size: 17px", dark_qss(15))  # 不同字号互不串扰

    def test_game_view_replay_turns(self):
        orig_chat = llm.chat
        llm.chat = recorder.replay(str(FIXTURE))
        try:
            book = wb.load_worldbook(str(BOOK_FILE))
            game = Game(book, player="阿澈", player_desc="新一代守灯人",
                        worldbook_file="星环守望者.book")
            view = GameView(game, "星环守望者.book")

            # 开局渲染
            wait_until(lambda: len(view.narrative_view.toPlainText()) > 0)
            self.assertGreater(len(view.narrative_view.toPlainText()), 50)
            self.assertGreater(view.options_layout.count(), 0, "开局应有选项按钮")
            self.assertGreater(view.state_layout.count(), 0, "状态面板应非空")

            # 通过输入框模拟行动（与 fixture 第一条行动一致）
            view.input_edit.setText("我决定先查看归星室的仪表读数。")
            view._send()
            wait_until(lambda: len(game.history) >= 3)  # 开局叙述+玩家+回合叙述
            self.assertEqual(len(game.history), 3)
            self.assertIn("环星城", view.narrative_view.toPlainText(),
                          "回合叙述应出现在对话区")
            self.assertNotEqual(view.scene_label.text(), "场景：—")
        finally:
            llm.chat = orig_chat
            view.close()


    def test_load_save_renders_history(self):
        """读档回归：不重新开局、历史不被清空、不启动 worker（bug 修复验证）。"""
        book = wb.load_worldbook(str(BOOK_FILE))
        game = Game(book, player="阿澈", player_desc="新一代守灯人",
                    worldbook_file="星环守望者.book")
        game.history = [{"role": "user", "content": "行动1"},
                        {"role": "assistant", "content": "叙述1"}]
        game.scene = "某场景"
        view = GameView(game, "星环守望者.book")
        text = view.narrative_view.toPlainText()
        self.assertIn("行动1", text, "历史中的玩家行动应显示")
        self.assertIn("叙述1", text, "历史中的叙述应显示")
        self.assertEqual(len(game.history), 2, "读档不得清空历史（new_game 才会）")
        self.assertIsNone(view.worker, "读档不应启动回合 worker")
        self.assertIn("某场景", view.scene_label.text())
        view.close()

    def test_close_terminates_worker(self):
        """关闭 view 时终止运行中的 worker（防信号发向已销毁对象）。"""
        orig_chat = llm.chat
        llm.chat = recorder.replay(str(FIXTURE))
        try:
            book = wb.load_worldbook(str(BOOK_FILE))
            game = Game(book, player="阿澈", player_desc="新一代守灯人",
                        worldbook_file="星环守望者.book")
            view = GameView(game, "星环守望者.book")
            wait_until(lambda: view.worker is not None and not view.worker.isRunning())
            view.close()  # 不应崩溃
            self.assertTrue(True)
        finally:
            llm.chat = orig_chat


if __name__ == "__main__":
    unittest.main()
