"""离线逻辑测试：TXT 分块与编码探测。"""
import tempfile
import unittest
from pathlib import Path

from bookrpg.parser import txt_parser


class TestChunkText(unittest.TestCase):
    def test_small_text_single_chunk(self):
        chunks = txt_parser.chunk_text("第一段。\n\n第二段。")
        self.assertEqual(len(chunks), 1)
        self.assertIn("第一段", chunks[0])

    def test_large_text_multiple_chunks(self):
        para = "甲" * 2000
        text = "\n\n".join([para] * 5)  # ~10000 字
        chunks = txt_parser.chunk_text(text)
        self.assertGreaterEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), txt_parser.CHUNK_SIZE + 2000)

    def test_blank_lines_merged(self):
        chunks = txt_parser.chunk_text("一段\n\n\n\n另一段")
        self.assertEqual(len(chunks), 1)
        self.assertIn("另一段", chunks[0])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            txt_parser.chunk_text("   \n\n  ")


class TestParseEncoding(unittest.TestCase):
    def _write(self, data: bytes) -> str:
        f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        f.write(data)
        f.close()
        return f.name

    def test_utf8(self):
        p = self._write("你好世界".encode("utf-8"))
        try:
            chunks = txt_parser.parse(p)
            self.assertIn("你好世界", chunks[0])
        finally:
            Path(p).unlink(missing_ok=True)

    def test_gbk(self):
        p = self._write("中文测试".encode("gb18030"))
        try:
            chunks = txt_parser.parse(p)
            self.assertIn("中文测试", chunks[0])
        finally:
            Path(p).unlink(missing_ok=True)

    def test_utf8_bom(self):
        p = self._write("\ufeff带BOM".encode("utf-8"))
        try:
            chunks = txt_parser.parse(p)
            self.assertNotIn("\ufeff", chunks[0])
        finally:
            Path(p).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
