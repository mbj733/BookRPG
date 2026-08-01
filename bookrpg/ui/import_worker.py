"""导入 worker：后台线程跑全书通读，进度信号回主线程，界面不卡。支持取消。"""
import math
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from bookrpg import worldbook as wb
from bookrpg.parser import epub_parser, pdf_parser, txt_parser

if getattr(sys, "frozen", False):
    # PyInstaller 打包版：__file__ 指向临时解压目录（只读、会被清理），
    # 书库必须放在 exe 旁——用户的书/存档在那里可见可写
    BOOKS_DIR = Path(sys.executable).resolve().parent / "books"
else:
    # 源码版：项目根
    BOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "books"

# 每块精读调用的经验耗时（reasoning 模型，秒），用于导入前预估
SECONDS_PER_CALL = 3.0


class ImportCancelled(Exception):
    """用户取消导入。"""


class ImportWorker(QThread):
    progress = Signal(str)     # 阶段文字："解析书籍…" / "精读 3/14" / "汇总 1/3" / "写入书库…"
    finished_ok = Signal(str)  # 成功：.book 文件绝对路径
    failed = Signal(str)       # 失败原因（含用户取消）

    def __init__(self, source: str, story: str | None, parent=None):
        super().__init__(parent)
        self.source = source
        self.story = story
        self._cancel = threading.Event()

    def cancel(self):
        """请求取消：已提交的精读批次会快速返回，聚合前停止。"""
        self._cancel.set()

    def _count_chunks(self) -> int:
        """快速解析估算块数（build_worldbook 内部会再解析一次，此处只为预估）。"""
        p = Path(self.source)
        if p.suffix.lower() == ".epub":
            _, _, text = epub_parser.extract(self.source, story_filter=self.story)
            return len(txt_parser.chunk_text(text))
        if p.suffix.lower() == ".pdf":
            _, _, text = pdf_parser.extract(self.source, story_filter=self.story)
            return len(txt_parser.chunk_text(text))
        return len(txt_parser.parse(self.source))

    def _estimate(self, chunks: int) -> str:
        """估算调用次数与耗时。"""
        n_calls = chunks + 1  # 精读 + 聚合（短书）
        if chunks > wb.GROUP_SIZE:
            n_calls += math.ceil(chunks / wb.GROUP_SIZE)  # 分层组数
        minutes = max(1, round(n_calls * SECONDS_PER_CALL / 60))
        return (f"全书 {chunks} 块 → 约 {n_calls} 次 AI 调用，"
                f"预计 {minutes} 分钟（仅供参考）")

    def run(self):
        try:
            self.progress.emit("解析书籍…")
            try:
                chunks = self._count_chunks()
                if not self._cancel.is_set():
                    self.progress.emit(self._estimate(chunks))
            except Exception:
                pass  # 预估失败不阻塞导入
            book = wb.build_worldbook(self.source, story=self.story,
                                      progress=self._progress_cb,
                                      cache_dir=BOOKS_DIR / ".cache")
            self.progress.emit("写入书库…")
            out = wb.save_worldbook(book, BOOKS_DIR)
            self.finished_ok.emit(str(out))
        except ImportCancelled as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(str(e))

    def _progress_cb(self, i, total, stage):
        if self._cancel.is_set():
            raise ImportCancelled("用户取消导入")
        self.progress.emit(f"{stage} {i}/{total}")
