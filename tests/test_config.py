"""离线逻辑测试：config 保存/读取（隔离 CONFIG_PATH，绝不碰真实 config.json）。"""
import json
import tempfile
import unittest
from pathlib import Path

from bookrpg import config


class TestConfigSaveLoad(unittest.TestCase):
    def setUp(self):
        self._orig_path = config.CONFIG_PATH

    def tearDown(self):
        config.CONFIG_PATH = self._orig_path

    def test_save_merges_existing_fields(self):
        """合并写入：未提及字段保留（不覆盖丢失）。"""
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "config.json"
            config.CONFIG_PATH = fake
            fake.write_text(json.dumps({"api_key": "sk-user-filled", "model": "m1"}),
                            encoding="utf-8")
            config.save({"font_size": 17, "base_url": "https://x/v1"})
            data = json.loads(fake.read_text(encoding="utf-8"))
            self.assertEqual(data["api_key"], "sk-user-filled")  # 已存在字段保留
            self.assertEqual(data["model"], "m1")
            self.assertEqual(data["font_size"], 17)              # 新字段写入
            self.assertEqual(data["base_url"], "https://x/v1")

    def test_font_size_default(self):
        """config.json 不存在时 font_size 回落默认 15。"""
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_PATH = Path(d) / "config.json"  # 不存在
            self.assertEqual(config.load()["font_size"], 15)

    def test_font_size_roundtrip(self):
        """保存 font_size 后能读回。"""
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_PATH = Path(d) / "config.json"
            config.save({"font_size": 17})
            self.assertEqual(config.load()["font_size"], 17)

    def test_thinking_mode_derived_from_provider(self):
        """config.json 未存 thinking_mode 时按 provider 推导（已知供应商）。"""
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_PATH = Path(d) / "config.json"
            config.save({"provider": "阿里云百炼（通义千问）"})
            self.assertEqual(config.load()["thinking_mode"], "qwen")

    def test_thinking_mode_unknown_provider_none(self):
        """未知供应商 → thinking_mode 兜底 none（不发任何适配参数）。"""
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_PATH = Path(d) / "config.json"
            config.save({"provider": "某未知网关"})
            self.assertEqual(config.load()["thinking_mode"], "none")

    def test_font_family_default(self):
        """font_family 默认微软雅黑。"""
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_PATH = Path(d) / "config.json"  # 不存在
            self.assertEqual(config.load()["font_family"], "Microsoft YaHei UI")


if __name__ == "__main__":
    unittest.main()
