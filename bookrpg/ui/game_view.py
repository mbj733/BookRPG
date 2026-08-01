"""游戏界面：左侧状态面板 + 中间对话区 + 选项按钮 + 底部输入区 + 场景栏。

LLM 请求在 GameWorker（QThread）里跑，期间禁用输入防并发；
回合完成信号回主线程渲染。
"""
import html as html_mod
import re

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QTextBrowser,
                               QVBoxLayout, QWidget)

from bookrpg import config
from bookrpg.providers import font_size_offset

from bookrpg.engine import Game
from bookrpg.save import save_game
from bookrpg.state import GameState


def _md_to_html(text: str) -> str:
    """极简 markdown → HTML 片段（QTextBrowser.append 支持 HTML）。

    覆盖：**加粗**、*斜体*、> 引用行、换行。先转义再套标签，防注入。
    """
    t = html_mod.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"^&gt;\s?(.+)$", r'<span style="color:#81a1c1;">\1</span>', t, flags=re.M)
    return t.replace("\n", "<br>")


# 对话区三段式配色：模型输出=暖白，玩家输入=金色文字+深色背景块，错误=朱红
def _player_html(text: str) -> str:
    """玩家输入：深色背景块 + 金色文字 + ▸ 标记（背景色 Qt rich text 原生支持）。"""
    esc = html_mod.escape(text).replace("\n", "<br>")
    return (f'<div style="background-color:#1a2233; color:#d9a441; padding:10px 12px;">'
            f'<b>▸ 你：</b>　{esc}</div>')


def _narr_html(text: str) -> str:
    """模型输出：暖白正文，按空行拆成多个小段落（段间留白），与玩家输入留间距。"""
    t = _md_to_html(text)
    paras = [p for p in re.split(r"<br>\s*<br>", t) if p.strip()]
    inner = "".join(f'<div style="margin:0 0 10px 0;">{p}</div>' for p in paras)
    return f'<div style="color:#e6e2d6; margin-top:6px;">{inner}</div>'


def _err_html(text: str) -> str:
    """错误提示：朱红。"""
    esc = html_mod.escape(text)
    return f'<div style="color:#c25b4e; margin-top:8px;">**[错误]** {esc}</div>'


class GameWorker(QThread):
    turn_done = Signal(dict)
    failed = Signal(str)

    def __init__(self, game: Game, action: str, is_new: bool, parent=None):
        super().__init__(parent)
        self.game = game
        self.action = action
        self.is_new = is_new

    def run(self):
        try:
            r = self.game.new_game() if self.is_new else self.game.step(self.action)
            self.turn_done.emit(r)
        except Exception as e:
            self.failed.emit(str(e))


class GameView(QWidget):
    back_to_library = Signal()
    settings_saved = Signal()  # 设置已保存（MainWindow 据此即时重设主题）

    def __init__(self, game: Game, worldbook_file: str | None = None):
        super().__init__()
        self.game = game
        self.worker: GameWorker | None = None
        self._last_options: list[str] = []
        self._prev_state: dict | None = None  # 状态变化高亮用

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        # 顶栏：书名/角色 + 场景横幅（渐变金墨底）
        hero = QFrame()
        hero.setObjectName("heroStrip")
        hero_lay = QHBoxLayout(hero)
        hero_lay.setContentsMargins(20, 12, 14, 12)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = f"{game.worldbook.get('book', {}).get('title', '未知')} · 扮演 {game.player}"
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sceneTitle")
        self.scene_label = QLabel("场景：—")
        self.scene_label.setObjectName("sceneSub")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.scene_label)
        hero_lay.addLayout(title_box)
        hero_lay.addStretch(1)
        self.save_btn = QPushButton("💾 存档")
        self.settings_btn = QPushButton("⚙️ 设置")
        self.back_btn = QPushButton("← 书库")
        # clicked 会传 checked 参数，必须用 lambda 包裹，否则 bool 会进 name_hint
        self.save_btn.clicked.connect(lambda: self._save())
        self.settings_btn.clicked.connect(self._open_settings)
        self.back_btn.clicked.connect(self.back_to_library.emit)
        hero_lay.addWidget(self.save_btn)
        hero_lay.addWidget(self.settings_btn)
        hero_lay.addWidget(self.back_btn)
        root.addWidget(hero)

        body = QHBoxLayout()
        body.setStretch(0, 3)

        # 中间：对话区 + 选项 + 输入
        mid = QVBoxLayout()
        self.narrative_view = QTextBrowser()
        self.narrative_view.setOpenExternalLinks(False)
        # 自动滚到底的最终保障：布局/内容变化使滚动范围更新时，必然回到底部。
        # （append 后 maximum 滞后更新，固定延迟不可靠——rangeChanged 在布局完成时触发）
        _sb = self.narrative_view.verticalScrollBar()
        _sb.rangeChanged.connect(lambda _mn, _mx: _sb.setValue(_mx))
        self.options_box = QWidget()
        self.options_layout = QVBoxLayout(self.options_box)
        self.options_layout.setContentsMargins(0, 4, 0, 4)
        self.options_layout.setSpacing(6)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("输入你的行动…（回车发送；支持 save 存档 / state 看状态）")
        self.input_edit.returnPressed.connect(self._send)
        self.send_btn = QPushButton("行动")
        self.send_btn.setObjectName("primaryBtn")
        self.send_btn.clicked.connect(self._send)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self.send_btn)
        mid.addWidget(self.narrative_view, 1)
        mid.addWidget(self.options_box)
        mid.addLayout(input_row)
        body.addLayout(mid, 3)

        # 右侧：状态面板（每属性一张卡片，键名小标题在上、数值在下）
        state_col = QWidget()
        state_col_lay = QVBoxLayout(state_col)
        state_col_lay.setContentsMargins(0, 0, 0, 0)
        state_col_lay.setSpacing(8)
        state_title = QLabel("◈ 角色状态")
        state_title.setObjectName("sectionLabel")
        state_col_lay.addWidget(state_title)
        panel = QWidget()
        self.state_layout = QVBoxLayout(panel)
        self.state_layout.setContentsMargins(6, 4, 6, 8)
        self.state_layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setObjectName("stateScroll")
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(250)
        self.state_scroll = scroll  # 状态更新后滚动到底
        state_col_lay.addWidget(scroll, 1)
        body.addWidget(state_col, 1)

        root.addLayout(body, 1)

        if game.history:
            # 读档：不重新开局（new_game 会清空历史），直接重建对话区
            self._render_history()
        else:
            # 新游戏：开局
            self._set_busy(True)
            self.worker = GameWorker(game, "", is_new=True)
            self.worker.turn_done.connect(self._on_turn_done)
            self.worker.failed.connect(self._on_failed)
            self.worker.start()

    def _render_history(self):
        """从存档历史重建对话区（读档路径，绝不调用 new_game）。"""
        if self.game.summary:
            # 长局压缩的前情提要：读档时先展示，帮助玩家衔接剧情
            self.narrative_view.append(_narr_html(f"**【前情提要】**\n\n{self.game.summary}"))
            self.narrative_view.append("")
        for m in self.game.history:
            if m["role"] == "user":
                self.narrative_view.append(_player_html(m["content"]))
            else:
                self.narrative_view.append(_narr_html(m["content"]))
                self.narrative_view.append("")
        self.scene_label.setText(f"场景：{self.game.scene}" if self.game.scene else "场景：—")
        self._append_state()
        self._scroll_to_end()
        if self.game.game_over:
            self.narrative_view.append(_md_to_html(f"\n**【结局】** {self.game.game_over}"))
            self.input_edit.setEnabled(False)
            self.send_btn.setEnabled(False)
            self._show_ending_buttons()
            return
        # 读档恢复当前回合的可选行动
        self._last_options = list(self.game.options)
        if self._last_options:
            for opt in self._last_options:
                btn = QPushButton(opt)
                btn.setObjectName("optionBtn")
                btn.clicked.connect(lambda _, o=opt: self._choose(o))
                self.options_layout.addWidget(btn)
            self.options_box.setVisible(True)
        else:
            self.options_box.setVisible(False)

    def closeEvent(self, event):
        """关闭时终止仍在运行的 worker，避免信号发向已销毁对象。"""
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(2000)
        super().closeEvent(event)

    # ---------- 渲染 ----------

    def _scroll_to_end(self):
        """对话区滚动到底：同步设一次 + 事件循环空闲补滚一次（跟手）。

        maximum 在 append 后滞后布局事件才更新，同步 setValue 可能停在半路；
        最终保障是 __init__ 里连接的 rangeChanged 信号——布局完成时自动回底。
        """
        sb = self.narrative_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def _show_ending_buttons(self):
        """结局出口：重新开始 / 返回书库。"""
        while self.options_layout.count():
            item = self.options_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        restart_btn = QPushButton("🔄 重新开始")
        back_btn = QPushButton("← 返回书库")
        restart_btn.clicked.connect(self._restart)
        back_btn.clicked.connect(self.back_to_library.emit)
        self.options_layout.addWidget(restart_btn)
        self.options_layout.addWidget(back_btn)
        self.options_box.setVisible(True)
        self._scroll_to_end()

    def _restart(self):
        """清空进度重新开局（同一本书、同一角色）。"""
        self.game.history = []
        self.game.game_over = None
        self.game.state = GameState()
        self.narrative_view.clear()
        self.scene_label.setText("场景：—")
        self._set_busy(True)
        self.worker = GameWorker(self.game, "", is_new=True)
        self.worker.turn_done.connect(self._on_turn_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_turn_done(self, r: dict):
        self._set_busy(False)
        self._render(r)

    def _on_failed(self, msg: str):
        self._set_busy(False)
        self.narrative_view.append(_err_html(msg))
        self._append_state()
        self._scroll_to_end()

    def _render(self, r: dict):
        if r.get("scene"):
            self.scene_label.setText(f"场景：{r['scene']}")
        md = r.get("narrative", "")
        self.narrative_view.append(_narr_html(md))
        self.narrative_view.append("")  # 与下一条消息的间隔
        self._append_state()
        self._scroll_to_end()

        # 选项按钮（重建）
        self._last_options = r.get("options", [])
        while self.options_layout.count():
            item = self.options_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, opt in enumerate(self._last_options, 1):
            btn = QPushButton(f"{i}. {opt}")
            btn.setObjectName("optionBtn")
            btn.clicked.connect(lambda _=False, o=opt: self._choose(o))
            self.options_layout.addWidget(btn)
        self.options_box.setVisible(bool(self._last_options))

        if r.get("game_over"):
            self.narrative_view.append(_md_to_html(f"\n**【结局】** {r['game_over']}"))
            self.input_edit.setEnabled(False)
            self.send_btn.setEnabled(False)
            self._show_ending_buttons()
            return  # 结局后不再渲染选项

    @staticmethod
    def _fmt_state_value(v) -> str:
        """状态值显示：列表逐条换行、字典逐项、其余直接显示（不加·前缀，保持清爽）。"""
        if isinstance(v, list):
            return "\n".join(str(x) for x in v)
        if isinstance(v, dict):
            return "\n".join(f"{k}：{x}" for k, x in v.items())
        return str(v)

    @staticmethod
    def _is_empty_state(v) -> bool:
        """空状态值：None / 空串 / 空列表 / 空字典（不显示，获得后才出现）。"""
        if v is None:
            return True
        if isinstance(v, str) and not v.strip():
            return True
        if isinstance(v, (list, dict)) and len(v) == 0:
            return True
        return False

    def _append_state(self):
        """重建状态面板：每属性一张卡片（键名小标题在上、数值在下），本轮变化的属性金边高亮。

        滚动位置保持：重建前记录滚动比例，布局完成后恢复——状态面板不自动滚到底，
        用户可自由滚动查看历史状态（重建后 maximum 滞后布局事件，用 singleShot(0) 恢复）。
        """
        sb = self.state_scroll.verticalScrollBar()
        old_max = sb.maximum()
        old_ratio = (sb.value() / old_max) if old_max > 0 else 0.0
        while self.state_layout.count():
            item = self.state_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        cur = self.game.state.to_dict()
        prev = self._prev_state
        changed = {k for k, v in cur.items() if prev is None or prev.get(k) != v}
        self._prev_state = cur
        for k, v in cur.items():
            if self._is_empty_state(v):
                continue  # 空值不显示：获得后才出现，失去后消失
            card = QFrame()
            card.setObjectName("stateCard")
            if k in changed:
                # 变化高亮：动态属性走 QSS（金边 + 浅底），设置后须重新 polish 才生效
                card.setProperty("changed", True)
                card.style().unpolish(card)
                card.style().polish(card)
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 8, 12, 10)
            lay.setSpacing(4)
            kl = QLabel(str(k))
            kl.setObjectName("stateKey")
            vl = QLabel(self._fmt_state_value(v))
            vl.setObjectName("stateVal")
            vl.setWordWrap(True)
            lay.addWidget(kl)
            lay.addWidget(vl)
            self.state_layout.addWidget(card)
        self.state_layout.addStretch(1)
        # 布局完成后按比例恢复滚动位置（不自动滚到底，保持用户查看位置）
        QTimer.singleShot(0, lambda: sb.setValue(
            int(old_ratio * sb.maximum()) if sb.maximum() > 0 else 0))

    # ---------- 交互 ----------

    def _open_settings(self):
        """游戏页直接改设置（字号等），保存后即时生效，无需回书库/重启。"""
        from bookrpg.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        # saved 带 font_size 参数；settings_saved 无参转发需丢弃参数（直接 connect 会 TypeError）
        dlg.saved.connect(lambda _fs: self.settings_saved.emit())
        dlg.exec()

    def refresh_font(self):
        """字号/主题变更后即时生效：重设文档字号 + 重渲染对话区。

        QTextDocument 对已渲染 HTML 的字号是快照（append 时固化），
        只改 QSS 旧消息不变——必须重渲染让全部消息用新字号。
        """
        cfg = config.load()
        fs = int(cfg.get("font_size", 15))
        family = cfg.get("font_family") or "Microsoft YaHei UI"
        fs += font_size_offset(family)  # 字面补偿（楷体/宋体偏小，视觉拉齐）
        font = QFont(family)
        font.setPixelSize(fs)  # 与 QSS 的 px 单位一致
        font.setHintingPreference(QFont.PreferNoHinting)  # 平滑渲染（老 CJK 字体 hinting 差）
        self.narrative_view.document().setDefaultFont(font)
        # 重建对话区（含前情/历史/选项/状态），复用读档渲染路径
        self.narrative_view.clear()
        while self.options_layout.count():
            item = self.options_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._render_history()
        self._scroll_to_end()

    def _choose(self, opt: str):
        """点选项：填入输入框等待确认（不自动发送），由玩家点「行动」或回车发出。"""
        self.input_edit.setText(opt)
        self.input_edit.setFocus()

    def _send(self):
        if self.worker and self.worker.isRunning():
            return
        text = self.input_edit.text().strip()
        if not text:
            return
        low = text.lower()
        if low.startswith("save"):
            self._save(text)
            return
        if low == "state":
            self._append_state()
            return
        # 玩家行动先上屏，再请求模型
        self.narrative_view.append(_player_html(text))
        self.narrative_view.append("")  # 与模型回应的间隔
        self._scroll_to_end()
        self.input_edit.clear()
        self._set_busy(True)
        self.worker = GameWorker(self.game, text, is_new=False)
        self.worker.turn_done.connect(self._on_turn_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _set_busy(self, busy: bool):
        self.input_edit.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.back_btn.setEnabled(not busy)  # 回合进行中禁止返回（防 worker 竞态）
        self.send_btn.setText("思考中…" if busy else "行动")  # 加载指示

    def _save(self, name_hint: str = ""):
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        from pathlib import Path
        if not isinstance(name_hint, str):  # 防御：clicked 信号的 checked 参数误传
            name_hint = ""
        name, ok = QInputDialog.getText(self, "存档", "存档名：",
                                        text=name_hint.removeprefix("save").strip())
        if not ok or not name.strip():
            return
        try:
            saves_root = Path(__file__).resolve().parent.parent.parent / "books" / "saves"
            book_dir = saves_root / (self.game.worldbook_file or "未知").removesuffix(".book")
            target = book_dir / f"{name.strip()}.json"
            if target.exists():
                ans = QMessageBox.question(
                    self, "覆盖存档",
                    f"存档「{name.strip()}」已存在，要覆盖吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ans != QMessageBox.Yes:
                    return
            save_game(self.game, target)
            QMessageBox.information(self, "存档", f"已存档：{book_dir.name}/{name.strip()}")
        except Exception as e:
            QMessageBox.warning(self, "存档失败", str(e))
