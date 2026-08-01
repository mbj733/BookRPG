"""离线逻辑测试：PDF 解析（PyMuPDF 现造 PDF）+ build_worldbook PDF 路径（fake chat）。"""
import tempfile
import unittest
from pathlib import Path

from bookrpg import llm, worldbook as wb
from bookrpg.parser import pdf_parser


def make_pdf(tmp: Path, name: str = "测试书.pdf", pages: int = 3) -> Path:
    """用 PyMuPDF 现造一个带文本层的 PDF（含作者元数据 + 页码行）。

    注意：insert_text 默认字体不含中文字形，必须嵌入中文字体
    （Windows 自带 msyh.ttc），否则中文会变成占位圆点。
    """
    import fitz
    FONT = r"C:\Windows\Fonts\msyh.ttc"
    doc = fitz.open()
    doc.set_metadata({"title": "测试书", "author": "测试作者"})
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"第{i + 1}页正文：春山如笑，溪水潺潺。少年踏上了旅途。",
                         fontsize=14, fontname="china-s", fontfile=FONT)
        page.insert_text((72, 780), f"{i + 1}", fontsize=10)  # 页脚页码
    p = tmp / name
    doc.save(p)
    doc.close()
    return p


def is_summarize(messages) -> bool:
    return "书籍精读引擎" in messages[0]["content"]


class TestPdfParser(unittest.TestCase):
    def test_extract_text_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            src = make_pdf(Path(d))
            author, labels, text = pdf_parser.extract(str(src))
            self.assertEqual(author, "测试作者")
            self.assertIn("春山如笑", text)
            self.assertEqual(text.count("春山如笑"), 3)  # 3 页正文都提取到
            self.assertNotRegex(text, r"(?m)^\d{1,4}$")  # 页码行已清除

    def test_empty_pdf_raises_clear_error(self):
        """无文本层（扫描版）→ 明确报错提示，不静默返回空。"""
        import fitz
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "扫描版.pdf"
            doc = fitz.open()
            doc.new_page()  # 空白页（无文字）
            doc.save(src)
            doc.close()
            with self.assertRaisesRegex(ValueError, "扫描版|OCR"):
                pdf_parser.extract(str(src))

    def test_build_worldbook_pdf_flow(self):
        """PDF 导入走通：提取 → 分块 → 精读(mock) → 聚合 → .book dict。"""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = make_pdf(tmp)
            orig = llm.chat_json
            calls = {"sum": 0, "agg": 0}
            def chat_json(messages, **kw):
                if is_summarize(messages):
                    calls["sum"] += 1
                    return {"summary": "摘要"}
                calls["agg"] += 1
                return {"book": {"title": "测试书", "author": "测试作者"},
                        "world": {"setting": "s", "rules": [], "locations": [], "factions": []},
                        "characters": [], "plot": {}, "items": [], "lore_notes": ""}
            llm.chat_json = chat_json
            try:
                book = wb.build_worldbook(str(src))
            finally:
                llm.chat_json = orig
            self.assertGreaterEqual(calls["sum"], 1)   # 有精读
            self.assertGreaterEqual(calls["agg"], 1)   # 有聚合
            self.assertEqual(book["book"]["title"], "测试书")
            self.assertEqual(book["book"]["author"], "测试作者")
            self.assertEqual(book["book"]["source"].lower().endswith(".pdf"), True)


if __name__ == "__main__":
    unittest.main()
