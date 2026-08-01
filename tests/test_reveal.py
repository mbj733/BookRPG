"""「待揭示」状态机制测试：开局不显示后期信息，剧情首次提到时自动登记（monkeypatch，不调 API）。"""
import unittest

from bookrpg import llm
from bookrpg.engine import Game

REVEAL_BOOK = {
    "book": {"title": "测试书"},
    "world": {"setting": "测试世界", "rules": ["规则1"], "locations": [], "factions": []},
    "characters": [
        {"name": "主角", "role": "主角", "personality": "勇敢"},
        {"name": "萧玄", "role": "配角", "personality": "豪迈", "relations": "萧家先祖"},
    ],
    "plot": {"outline": "大纲", "key_events": [], "themes": "", "original_ending": "结局"},
    "items": [],
    "lore_notes": "",
    "state_template": {
        "说明": "测试面板",
        "属性": {
            "生命": 100,
            "金钱": 50,
            "境界": "斗之气三段",
            "关系": {"萧战": "父亲"},
        },
        "待揭示": {
            "关系": {"萧玄": "萧家先祖，千年前斗帝血脉觉醒者"},
            "功法": ["黄阶低级功法《焚决》"],
            "物品": ["骨灵冷火"],
        },
    },
}


def make_game() -> Game:
    return Game(REVEAL_BOOK, player="主角", player_desc="勇敢的测试者")


def run_turn(g: Game, narrative: str, changes=None, opening: bool = False) -> dict:
    """跑一回合：mock llm.chat_json 返回固定叙述。"""
    orig = llm.chat_json
    llm.chat_json = lambda msgs, **kw: {
        "narrative": narrative, "options": ["a"], "state_changes": changes or {},
        "scene": "s", "game_over": None,
    }
    try:
        if opening:
            return g.new_game()
        return g.step("测试行动")
    finally:
        llm.chat_json = orig


class TestRevealPool(unittest.TestCase):
    def test_reveal_pool_loaded(self):
        g = make_game()
        self.assertIn("萧玄", g.reveal_pool.get("关系", {}))
        self.assertIn("黄阶低级功法《焚决》", g.reveal_pool.get("功法", []))

    def test_opening_state_excludes_hidden(self):
        """开局状态只含「属性」（已知部分），不含待揭示人物/功法。"""
        g = make_game()
        run_turn(g, "开场：你与父亲萧战坐在堂前。", opening=True)
        st = g.state.to_dict()
        self.assertIn("萧战", st["关系"])
        self.assertNotIn("萧玄", st["关系"])
        self.assertNotIn("功法", st)  # 属性里没有功法维度，待揭示的焚决也不该凭空出现

    def test_opening_narrative_does_not_reveal(self):
        """开局背景提到后期人物也不自动登记（避免泄露）。"""
        g = make_game()
        run_turn(g, "开场：萧家先祖萧玄的传说在加玛帝国流传。", opening=True)
        self.assertNotIn("萧玄", g.state.to_dict().get("关系", {}))

    def test_character_auto_revealed_when_mentioned(self):
        """非开局回合叙述首次提到人物 → 自动登记进关系。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        self.assertNotIn("萧玄", g.state.to_dict().get("关系", {}))
        run_turn(g, "药老缓缓开口：那位先祖，名讳萧玄。")
        st = g.state.to_dict()
        self.assertIn("萧玄", st["关系"])
        self.assertEqual(st["关系"]["萧玄"], "萧家先祖，千年前斗帝血脉觉醒者")

    def test_technique_auto_revealed_when_mentioned(self):
        """叙述提到《焚决》（带书名号）→ 自动加入功法列表。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        run_turn(g, "药老道：老夫可教你一门功法，名为《焚决》。")
        self.assertIn("黄阶低级功法《焚决》", g.state.to_dict().get("功法", []))

    def test_technique_revealed_without_book_marks(self):
        """叙述只写"焚决"（无书名号）也能命中揭示。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        run_turn(g, "焚决这门功法，吞噬异火为己用。")
        self.assertIn("黄阶低级功法《焚决》", g.state.to_dict().get("功法", []))

    def test_no_duplicate_reveal(self):
        """已登记的人物/功法不重复追加。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        run_turn(g, "萧玄来了。")
        run_turn(g, "萧玄又来了。")
        rel = g.state.to_dict()["关系"]
        self.assertEqual(rel["萧玄"], "萧家先祖，千年前斗帝血脉觉醒者")  # 单键
        self.assertEqual(len([k for k in rel if k == "萧玄"]), 1)
        self.assertEqual(len(g.state.to_dict().get("功法", [])), 0)  # 未提到不登记

    def test_player_input_triggers_reveal(self):
        """玩家主动问某人 → 该人物登记（玩家输入也参与匹配）。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {
            "narrative": "药老沉默不语。", "options": [], "state_changes": {},
            "scene": "s", "game_over": None,
        }
        try:
            g.step("萧玄到底是谁？")
        finally:
            llm.chat_json = orig
        self.assertIn("萧玄", g.state.to_dict().get("关系", {}))

    def test_alias_name_reveal(self):
        """人物名带括号别名（药老（药尘））：叙述只写别名也命中。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        # 覆盖 reveal_pool：加一个带别名的条目，叙述只用别名
        g.reveal_pool.setdefault("关系", {})["药老（药尘）"] = "戒指中的神秘灵魂"
        run_turn(g, "虚影开口：老夫药尘。")
        self.assertIn("药老（药尘）", g.state.to_dict().get("关系", {}))

    def test_model_state_changes_still_apply(self):
        """模型主动在 state_changes 登记关系时正常合并，不与自动揭示冲突。"""
        g = make_game()
        run_turn(g, "开场。", opening=True)
        run_turn(g, "剧情推进。", changes={"关系": {"云韵": "云岚宗宗主"}})
        st = g.state.to_dict()
        self.assertEqual(st["关系"]["云韵"], "云岚宗宗主")
        self.assertIn("萧战", st["关系"])  # 原有关系保留


if __name__ == "__main__":
    unittest.main()
