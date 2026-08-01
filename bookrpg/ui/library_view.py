"""书库界面：金色横幅 + 书架式卡片列表、导入新书（进度条）、创建游戏（选角色）、读取存档。"""
import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressDialog,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from bookrpg import worldbook as wb
from bookrpg.engine import Game
from bookrpg.save import load_game
from bookrpg.ui.import_worker import BOOKS_DIR, ImportWorker
from bookrpg.ui.settings_dialog import SettingsDialog


class CharacterDialog(QDialog):
    """选角色：原著角色列表 + 自捏。选完存 self.chosen_row（>=len(chars) 表示自捏）。"""

    def __init__(self, characters: list[dict], parent=None):
        super().__init__(parent)
        self.characters = characters
        self.chosen_row = -1
        self.setWindowTitle("选择扮演角色")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("选择要扮演的角色："))
        self.list_widget = QListWidget()
        for c in characters:
            name = c.get("name", "?")
            role = c.get("role", "")
            item = QListWidgetItem(f"{name}（{role}）")
            item.setSizeHint(QSize(0, 44))
            self.list_widget.addItem(item)
        item = QListWidgetItem("✍️ 自捏角色")
        item.setSizeHint(QSize(0, 44))
        self.list_widget.addItem(item)
        lay.addWidget(self.list_widget)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _accept(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self.chosen_row = row
        self.accept()


class LibraryView(QWidget):
    start_game = Signal(object, str)  # (Game, worldbook_file)
    load_save = Signal(object)        # (Game)
    settings_saved = Signal()         # 设置已保存（MainWindow 据此重设主题）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: ImportWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(8)

        # ---- 顶部金色横幅 ----
        hero = QFrame()
        hero.setObjectName("heroStrip")
        hero_lay = QHBoxLayout(hero)
        hero_lay.setContentsMargins(22, 14, 18, 14)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        app_title = QLabel("BookRPG · 书中世界")
        app_title.setObjectName("appTitle")
        tagline = QLabel("把一本书，变成你亲自踏入的世界")
        tagline.setObjectName("appTagline")
        title_box.addWidget(app_title)
        title_box.addWidget(tagline)
        hero_lay.addLayout(title_box)
        hero_lay.addStretch(1)
        self.setting_btn = QPushButton("⚙ 设置")
        self.setting_btn.clicked.connect(self._open_settings)
        hero_lay.addWidget(self.setting_btn)
        root.addWidget(hero)

        # ---- 书籍区 ----
        books_head = QHBoxLayout()
        books_head.addWidget(self._section_label("书 架"))
        books_head.addStretch(1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        books_head.addWidget(self.refresh_btn)
        root.addLayout(books_head)

        self.book_list = QListWidget()
        self.book_list.itemDoubleClicked.connect(lambda _: self._new_game())
        root.addWidget(self.book_list, 3)

        books_btns = QHBoxLayout()
        self.import_btn = QPushButton("📖 导入新书")
        self.import_btn.setObjectName("primaryBtn")
        self.play_btn = QPushButton("▶ 创建游戏")
        self.import_btn.clicked.connect(self._import_book)
        self.play_btn.clicked.connect(self._new_game)
        books_btns.addWidget(self.import_btn)
        books_btns.addWidget(self.play_btn)
        books_btns.addStretch(1)
        root.addLayout(books_btns)

        # ---- 存档区：按书分栏（每本书一个 tab） ----
        root.addWidget(self._section_label("存 档"))
        self.save_tabs = QTabWidget()
        self._save_sort_desc = True  # 默认按保存时间倒序
        root.addWidget(self.save_tabs, 2)

        saves_btns = QHBoxLayout()
        self.load_btn = QPushButton("📂 读取存档")
        self.delete_btn = QPushButton("🗑 删除存档")
        self.rename_btn = QPushButton("✏️ 重命名")
        self.sort_btn = QPushButton("⇅ 排序")
        self.load_btn.clicked.connect(self._load_save)
        self.delete_btn.clicked.connect(self._delete_save)
        self.rename_btn.clicked.connect(self._rename_save)
        self.sort_btn.clicked.connect(self._toggle_sort)
        saves_btns.addWidget(self.load_btn)
        saves_btns.addWidget(self.delete_btn)
        saves_btns.addWidget(self.rename_btn)
        saves_btns.addWidget(self.sort_btn)
        saves_btns.addStretch(1)
        root.addLayout(saves_btns)

        self.refresh()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    @staticmethod
    def _format_saved_at(s: str) -> str:
        """ISO 时间 → 纯中文日期："2026-08-01T03:11:00" → "2026年8月1日 03:11"。

        解析失败时原样返回（不阻塞列表显示）。
        """
        try:
            date_part, _, time_part = s.partition("T")
            y, m, d = date_part.split("-")
            hm = time_part[:5] if time_part else ""
            return f"{int(y)}年{int(m)}月{int(d)}日{(' ' + hm) if hm else ''}"
        except Exception:
            return s

    # ---------- 数据 ----------

    def refresh(self):
        self.book_list.clear()
        for b in sorted(BOOKS_DIR.glob("*.book")):
            try:
                book = wb.load_worldbook(b)
                meta = book.get("book", {})
                title = meta.get("title", b.stem)
                author = meta.get("author", "未知")
                text = f"《{title}》  {author}"
            except Exception:
                text = b.stem
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 54))
            item.setData(Qt.UserRole, str(b))
            self.book_list.addItem(item)

        # 空书架引导（不可选、居中、置灰）
        if self.book_list.count() == 0:
            ghost = QListWidgetItem("📖 书架空空如也——点击「导入新书」放入第一本书")
            ghost.setFlags(Qt.NoItemFlags)
            ghost.setTextAlignment(Qt.AlignCenter)
            ghost.setSizeHint(QSize(0, 120))
            self.book_list.addItem(ghost)

        # 存档：按书分栏（每本书一个 tab，栏内只显示该书存档）
        self.save_tabs.clear()
        saves_root = BOOKS_DIR / "saves"
        if saves_root.exists():
            for book_dir in sorted(saves_root.iterdir()):
                if not book_dir.is_dir():
                    continue
                saves = list(book_dir.glob("*.json"))
                if not saves:
                    continue
                self._add_save_tab(book_dir, saves)

    def _add_save_tab(self, book_dir: Path, saves: list[Path]):
        """为某本书建一个存档 tab（栏内列表 + 双击读档）。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(4)
        lst = QListWidget()
        lst.itemDoubleClicked.connect(lambda _: self._load_save())
        self._fill_save_list(lst, saves)
        lay.addWidget(lst)
        page._save_list = lst  # 供 _load_save/_delete_save 定位当前栏
        page._book_dir = book_dir
        self.save_tabs.addTab(page, book_dir.name)

    def _fill_save_list(self, lst: QListWidget, saves: list[Path]):
        """填充某书的存档列表，按 saved_at 排序（_save_sort_desc 控制升降序）。"""
        def sort_key(sp: Path):
            try:
                import json as _json
                return _json.loads(sp.read_text(encoding="utf-8")).get("saved_at", sp.stem)
            except Exception:
                return sp.stem
        saves = sorted(saves, key=sort_key, reverse=self._save_sort_desc)
        lst.clear()
        for sp in saves:
            info = sp.stem
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                player = data.get("player", "")
                saved_at = self._format_saved_at(str(data.get("saved_at", "")))
                parts = [p for p in (player, saved_at) if p]
                if parts:
                    info = f"{sp.stem} · {' · '.join(parts)}"
            except Exception:
                pass
            item = QListWidgetItem(info)
            item.setSizeHint(QSize(0, 44))
            item.setData(Qt.UserRole, str(sp))
            lst.addItem(item)

    def _current_save_list(self) -> QListWidget | None:
        page = self.save_tabs.currentWidget()
        return getattr(page, "_save_list", None) if page else None

    # ---------- 动作 ----------

    def _open_settings(self):
        dlg = SettingsDialog(self)
        # saved 带 font_size 参数；settings_saved 无参转发需丢弃参数（直接 connect 会 TypeError）
        dlg.saved.connect(lambda _fs: self.settings_saved.emit())
        dlg.exec()

    def _import_book(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择书籍", "", "书籍文件 (*.txt *.epub *.pdf);;所有文件 (*)")
        if not path:
            return
        p = Path(path)
        story = None
        if p.suffix.lower() == ".epub":
            story, ok = QInputDialog.getText(
                self, "选择篇目（可选）",
                "EPUB 合集：输入要导入的篇目关键词（留空=导入全本）：")
            if not ok:
                return
            story = story.strip() or None

        self.worker = ImportWorker(path, story)
        progress = QProgressDialog("正在通读全书…", "取消", 0, 0, self)
        progress.setWindowTitle("导入中")
        progress.setMinimumDuration(0)

        def on_cancel():
            progress.setLabelText("正在取消…")
            progress.setCancelButton(None)  # 防重复点击

        progress.canceled.connect(on_cancel)
        progress.canceled.connect(self.worker.cancel)

        def on_progress(text: str):
            progress.setLabelText(text)

        def on_ok(book_file: str):
            progress.close()
            QMessageBox.information(self, "导入完成", f"世界观包已生成：\n{book_file}")
            self.refresh()

        def on_fail(msg: str):
            progress.close()
            QMessageBox.warning(self, "导入失败", msg)
            self.refresh()

        self.worker.progress.connect(on_progress)
        self.worker.finished_ok.connect(on_ok)
        self.worker.failed.connect(on_fail)
        self.worker.start()

    def _selected_book(self) -> Path | None:
        item = self.book_list.currentItem()
        if not item:
            return None
        return Path(item.data(Qt.UserRole))

    def _new_game(self):
        bp = self._selected_book()
        if not bp:
            QMessageBox.information(self, "提示", "先在书库中选择一本书（双击或选中后点创建游戏）。")
            return
        try:
            book = wb.load_worldbook(bp)
        except Exception as e:
            QMessageBox.warning(self, "读取失败", str(e))
            return
        chars = book.get("characters", [])
        dlg = CharacterDialog(chars, self)
        if not dlg.exec():
            return
        if dlg.chosen_row >= len(chars):
            name, ok1 = QInputDialog.getText(self, "自捏角色", "名字（回车=主角）：")
            if not ok1:
                return
            name = name.strip() or "主角"
            desc, ok2 = QInputDialog.getText(self, "自捏角色", "一句话性格（回车=自由行动的主角）：")
            if not ok2:
                return
            desc = desc.strip() or "自由行动的主角"
        else:
            c = chars[dlg.chosen_row]
            name = c.get("name", "主角")
            desc = c.get("personality", "自由行动的主角")[:60]
        game = Game(book, player=name, player_desc=desc, worldbook_file=bp.name)
        self.start_game.emit(game, bp.name)

    def _load_save(self):
        lst = self._current_save_list()
        item = lst.currentItem() if lst else None
        if not item:
            QMessageBox.information(self, "提示", "先在当前书籍的存档栏中选择一个存档。")
            return
        try:
            game = load_game(Path(item.data(Qt.UserRole)), BOOKS_DIR)
        except Exception as e:
            QMessageBox.warning(self, "读档失败", str(e))
            return
        self.load_save.emit(game)

    def _delete_save(self):
        lst = self._current_save_list()
        item = lst.currentItem() if lst else None
        if not item:
            QMessageBox.information(self, "提示", "先选择要删除的存档。")
            return
        sp = Path(item.data(Qt.UserRole))
        ans = QMessageBox.question(
            self, "删除存档", f"确定删除存档「{sp.stem}」吗？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        try:
            sp.unlink()
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))

    def _rename_save(self):
        lst = self._current_save_list()
        item = lst.currentItem() if lst else None
        if not item:
            QMessageBox.information(self, "提示", "先选择要重命名的存档。")
            return
        sp = Path(item.data(Qt.UserRole))
        new_name, ok = QInputDialog.getText(self, "重命名存档", "新存档名：", text=sp.stem)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == sp.stem:
            return
        target = sp.with_name(f"{new_name}.json")
        if target.exists():
            QMessageBox.warning(self, "重命名失败", f"存档「{new_name}」已存在。")
            return
        try:
            sp.rename(target)
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, "重命名失败", str(e))

    def _toggle_sort(self):
        self._save_sort_desc = not self._save_sort_desc
        for i in range(self.save_tabs.count()):
            page = self.save_tabs.widget(i)
            lst = getattr(page, "_save_list", None)
            if lst is not None:
                self._fill_save_list(lst, list(page._book_dir.glob("*.json")))
        self.sort_btn.setText("⇅ 时间↑" if self._save_sort_desc else "⇅ 时间↓")
