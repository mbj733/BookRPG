"""离线逻辑测试：EPUB 单篇切片（依赖真实合集文件，缺失则跳过）。"""
import unittest
from pathlib import Path

from bookrpg.parser import epub_parser
from bookrpg.parser.txt_parser import chunk_text

EPUB = Path(__file__).resolve().parent.parent / "特德·姜作品中英文合集.epub"


@unittest.skipUnless(EPUB.exists(), f"缺少测试用 EPUB：{EPUB}")
class TestEpubSlice(unittest.TestCase):
    def test_story_extraction(self):
        author, _, text = epub_parser.extract(str(EPUB), story_filter="你一生的故事")
        self.assertIn("特德·姜", author)
        self.assertGreater(len(text), 30000)   # 中文全篇应 >3 万字
        self.assertIn("你一生的故事", text[:100])
        self.assertIn("李克勤", text[-500:])   # 结尾应为译者注

    def test_no_other_stories_mixed(self):
        _, _, text = epub_parser.extract(str(EPUB), story_filter="你一生的故事")
        for name in ["巴比伦塔", "除以零", "领悟", "人类科学之演变", "七十二个字母",
                     "地狱是上帝", "商人和炼金术士", "软件体", "赏心悦目"]:
            self.assertNotIn(name, text, f"混入了其他篇目：{name}")

    def test_chunking(self):
        _, _, text = epub_parser.extract(str(EPUB), story_filter="你一生的故事")
        chunks = chunk_text(text)
        self.assertGreaterEqual(len(chunks), 10)  # ~3.7万字 → ≥10 块

    def test_list_stories(self):
        labels = epub_parser.list_stories(str(EPUB))
        self.assertGreater(len(labels), 30)
        self.assertIn("你一生的故事（1998）", labels)

    def test_keyword_not_found(self):
        with self.assertRaises(ValueError):
            epub_parser.extract(str(EPUB), story_filter="不存在的篇目XYZ")


class TestLabelCore(unittest.TestCase):
    def test_strip_year(self):
        self.assertEqual(epub_parser._label_core("你一生的故事（1998）"), "你一生的故事")
        self.assertEqual(epub_parser._label_core("领悟 (1991)"), "领悟")

    def test_no_year(self):
        self.assertEqual(epub_parser._label_core("Story of Your Life"), "Story of Your Life")


if __name__ == "__main__":
    unittest.main()
