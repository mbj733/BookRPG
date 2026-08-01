"""「模板清洗」测试：sanitize_state_template 防「属性」与「待揭示」交叉污染。"""
import unittest

from bookrpg import worldbook as wb


def make_book(attrs=None, reveal=None) -> dict:
    return {
        "state_template": {
            "说明": "测试",
            "属性": attrs or {},
            "待揭示": reveal or {},
        }
    }


class TestSanitizeTemplate(unittest.TestCase):
    def test_known_character_removed_from_reveal(self):
        """属性关系已有人物（含别名变体）→ 待揭示同名条目删除。"""
        b = make_book(
            attrs={"关系": {"萧薰儿": "青梅竹马"}},
            reveal={"关系": {"萧薰儿（古薰儿）": "古族大小姐"}},
        )
        self.assertTrue(wb.sanitize_state_template(b))
        self.assertNotIn("萧薰儿（古薰儿）", b["state_template"]["待揭示"]["关系"])
        self.assertIn("萧薰儿", b["state_template"]["属性"]["关系"])  # 属性侧保留

    def test_unknown_character_kept_in_reveal(self):
        """属性关系没有的人物（萧玄）→ 待揭示保留。"""
        b = make_book(
            attrs={"关系": {"萧战": "父亲"}},
            reveal={"关系": {"萧玄": "萧家先祖"}},
        )
        self.assertFalse(wb.sanitize_state_template(b))
        self.assertIn("萧玄", b["state_template"]["待揭示"]["关系"])

    def test_technique_removed_from_attrs(self):
        """属性功法与待揭示功法同名（含书名号变体）→ 删属性侧（按未拥有处理）。"""
        b = make_book(
            attrs={"功法": ["黄阶低级功法《焚决》"]},
            reveal={"功法": ["黄阶低级功法《焚决》"]},
        )
        self.assertTrue(wb.sanitize_state_template(b))
        self.assertEqual(b["state_template"]["属性"]["功法"], [])
        self.assertIn("黄阶低级功法《焚决》", b["state_template"]["待揭示"]["功法"])

    def test_attrs_item_kept_when_not_in_reveal(self):
        """属性条目不在待揭示（如萧家功法）→ 保留。"""
        b = make_book(
            attrs={"功法": ["萧家基础功法"]},
            reveal={"功法": ["黄阶低级功法《焚决》"]},
        )
        self.assertFalse(wb.sanitize_state_template(b))
        self.assertEqual(b["state_template"]["属性"]["功法"], ["萧家基础功法"])

    def test_whole_book_style_case(self):
        """斗破苍穹式整书：属性关系有萧薰儿/药老在待揭示——萧薰儿删、药老留。"""
        b = make_book(
            attrs={"关系": {"萧薰儿": "青梅竹马（寄居萧家）"}, "物品": ["神秘戒指（母亲遗物）"]},
            reveal={"关系": {"萧薰儿（古薰儿）": "古族大小姐", "药老（药尘）": "戒指中的神秘灵魂"}},
        )
        wb.sanitize_state_template(b)
        reveal_rel = b["state_template"]["待揭示"]["关系"]
        self.assertNotIn("萧薰儿（古薰儿）", reveal_rel)  # 已认识 → 删
        self.assertIn("药老（药尘）", reveal_rel)          # 未现身 → 留

    def test_no_template_noop(self):
        self.assertFalse(wb.sanitize_state_template({"book": {}}))
        self.assertFalse(wb.sanitize_state_template({"state_template": {"属性": {}}}))

    def test_name_keys_alias_split(self):
        self.assertEqual(set(wb._name_keys("药老（药尘）")), {"药老（药尘）", "药老", "药尘"})
        self.assertEqual(set(wb._name_keys("萧玄")), {"萧玄"})


if __name__ == "__main__":
    unittest.main()
