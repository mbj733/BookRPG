"""主窗口：书库页 ↔ 游戏页 切换，深色史诗奇幻主题。

设计语言：墨蓝底 + 陈旧金点缀（书脊/金箔），楷体标题带书卷气，
书库卡片如书架排列。签名元素 = 卡片左侧金色书脊线。
"""
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QMainWindow, QStackedWidget

from bookrpg import config
from bookrpg.providers import font_size_offset
from bookrpg.ui.game_view import GameView
from bookrpg.ui.library_view import LibraryView

# 主题色板
INK = "#0d1117"        # 底色（深墨蓝黑）
PARCHMENT = "#161d2b"  # 面板（深蓝灰）
LINE = "#2a3446"       # 边框（冷灰蓝）
GOLD = "#d9a441"       # 强调（陈旧金，书脊/金箔）
MOON = "#7ea3c4"       # 次级（雾蓝）
IVORY = "#e6e2d6"      # 正文（暖象牙白）
CRIMSON = "#c25b4e"    # 结局/危险

def dark_qss(font_size: int = 15, font_family: str = "Microsoft YaHei UI") -> str:
    """深色主题 QSS。font_size：对话区字号；font_family：全局字体，均来自 config.json。

    注意：字体名必须加引号——"Microsoft YaHei UI" 等带空格的族名在 QSS 里
    无引号会被解析失败导致 fallback（曾致换字体后界面字体全乱）。
    对话区字号自动加字体补偿（楷体/宋体字面偏小，见 providers.FONT_SIZE_OFFSETS）。
    """
    fs = font_size + font_size_offset(font_family)  # 对话区（用户设定 + 补偿）
    off = font_size_offset(font_family)             # UI 全局固定字号补偿（楷体/宋体偏小）
    return f"""
QWidget {{
    background-color: {INK};
    color: {IVORY};
    font-family: "{font_family}";
    font-size: {14 + off}px;
}}
QLabel {{ background: transparent; }}

/* ---- 顶栏/横幅 ---- */
QLabel#appTitle {{
    font-family: "KaiTi";
    font-size: 30px;
    color: {GOLD};
    padding: 2px 0 0 4px;
}}
QLabel#appTagline {{
    color: {MOON};
    font-size: {12 + off}px;
    padding-left: 6px;
}}
QFrame#heroStrip {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #1a2437, stop:0.55 #10151f, stop:1 #1a2437);
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QLabel#sceneTitle {{
    font-family: "KaiTi";
    font-size: 19px;
    color: {GOLD};
}}
QLabel#sceneSub {{ color: {MOON}; font-size: {12 + off}px; }}
QLabel#sectionLabel {{
    color: {MOON};
    font-size: {12 + off}px;
    letter-spacing: 3px;
    padding: 10px 4px 4px 4px;
}}

/* ---- 书库卡片列表 ---- */
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: {PARCHMENT};
    border: 1px solid {LINE};
    border-left: 3px solid {GOLD};
    border-radius: 10px;
    margin: 6px 8px;
    padding: 12px 16px;
}}
QListWidget::item:hover {{ background: #1c2638; border-color: #3a4a66; }}
QListWidget::item:selected {{
    background: #22304a;
    border-color: {GOLD};
    border-left: 4px solid {GOLD};
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: #1d2739;
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 9px 18px;
    color: {IVORY};
}}
QPushButton:hover {{ background: #26334a; border-color: {GOLD}; color: {GOLD}; }}
QPushButton:pressed {{ background: #2e3d58; }}
QPushButton:disabled {{ color: #55617a; background: {PARCHMENT}; border-color: #222b3c; }}
QPushButton#primaryBtn {{
    background: {GOLD};
    color: {INK};
    font-weight: bold;
    border: 1px solid {GOLD};
}}
QPushButton#primaryBtn:hover {{ background: #e6b455; border-color: #e6b455; color: {INK}; }}
QPushButton#optionBtn {{
    background: #141b29;
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 11px 14px;
    text-align: left;
    font-size: {14 + off}px;
}}
QPushButton#optionBtn:hover {{ background: #1c2638; border-color: {GOLD}; }}

/* ---- 输入 ---- */
QLineEdit {{
    background: #10151f;
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 10px 12px;
    selection-background-color: {GOLD};
    selection-color: {INK};
}}
QLineEdit:focus {{ border-color: {GOLD}; }}

/* ---- 对话区 / 状态 ---- */
QTextBrowser {{
    background: #0f1520;
    border: 1px solid {LINE};
    border-radius: 12px;
    padding: 16px;
    font-size: {fs}px;
    selection-background-color: {GOLD};
    selection-color: {INK};
}}
QTextEdit {{
    background: #0f1520;
    border: 1px solid {LINE};
    border-radius: 12px;
    padding: 12px;
}}
/* 状态面板：每属性一张卡片（键名小标题在上、数值在下），变化时金边高亮 */
QFrame#stateCard {{
    background: #101826;
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QFrame#stateCard[changed="true"] {{
    background: #1a2233;
    border: 1px solid {GOLD};
}}
QFrame#stateCard QLabel#stateKey {{
    color: {GOLD};
    font-size: {12 + off}px;
    letter-spacing: 1px;
}}
QFrame#stateCard[changed="true"] QLabel#stateKey {{
    color: #e6b455;
    font-weight: bold;
}}
QFrame#stateCard QLabel#stateVal {{
    color: {IVORY};
    font-size: {13 + off}px;
    font-family: Consolas, "Microsoft YaHei UI";
}}
QScrollArea#stateScroll {{ background: transparent; border: none; }}
QScrollArea#stateScroll > QWidget > QWidget {{ background: transparent; }}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2a3446; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {GOLD}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2a3446; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- 进度/消息框统一 ---- */
QProgressDialog, QMessageBox, QDialog {{ background-color: {PARCHMENT}; }}
QMessageBox QLabel, QDialog QLabel {{ background: transparent; }}

/* ---- 存档标签页（QTabBar）---- */
QTabWidget::pane {{
    border: 1px solid {LINE};
    border-radius: 8px;
    background: #0f1520;
    top: -1px;
}}
QTabBar::tab {{
    background: {PARCHMENT};
    color: {MOON};
    border: 1px solid {LINE};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 20px;
    margin-right: 3px;
    min-width: 90px;
}}
QTabBar::tab:hover {{ background: #1c2638; color: {GOLD}; }}
QTabBar::tab:selected {{
    background: #0f1520;
    color: {GOLD};
    font-weight: bold;
}}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BookRPG · 书中世界")
        self.resize(1160, 800)
        cfg = config.load()
        self.setStyleSheet(dark_qss(cfg.get("font_size", 15),
                                    cfg.get("font_family", "Microsoft YaHei UI")))
        app_font = QFont("Microsoft YaHei UI", 10)
        # 禁用字体 hinting：DirectWrite 下老式 CJK 字体（楷体/宋体）hinting 不佳
        # 会渲染得又小又糊，PreferNoHinting 让其平滑渲染（QSS 不影响该偏好，全局生效）
        app_font.setHintingPreference(QFont.PreferNoHinting)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setFont(app_font)

        self.stack = QStackedWidget()
        self.library = LibraryView()
        self.stack.addWidget(self.library)
        self.setCentralWidget(self.stack)

        self.library.start_game.connect(self._open_game)
        self.library.load_save.connect(self._open_game)
        self.library.settings_saved.connect(self._reapply_theme)

    def _open_game(self, game, worldbook_file: str | None = None):
        if worldbook_file is None:  # 读档路径：信号只带 game，文件名从 game 取
            worldbook_file = getattr(game, "worldbook_file", None)
        view = GameView(game, worldbook_file)
        view.back_to_library.connect(self._back_to_library)
        view.settings_saved.connect(self._reapply_theme)
        # 主面板阴影，提升层级感
        shadow = QGraphicsDropShadowEffect(view)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 140))
        view.setGraphicsEffect(shadow)
        self.stack.addWidget(view)
        self.stack.setCurrentWidget(view)

    def _reapply_theme(self):
        """设置保存后即时重设主题（字号/字体等），无需重启。

        已打开的游戏页对话区需重渲染（QTextDocument 对旧 HTML 字号是快照）。
        """
        cfg = config.load()
        self.setStyleSheet(dark_qss(cfg.get("font_size", 15),
                                    cfg.get("font_family", "Microsoft YaHei UI")))
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if isinstance(w, GameView):
                w.refresh_font()

    def _back_to_library(self):
        current = self.stack.currentWidget()
        self.stack.removeWidget(current)
        current.deleteLater()
        self.library.refresh()
        self.stack.setCurrentWidget(self.library)
