"""离线逻辑测试：引擎提示词渲染、请求构造、状态更新、降级路径（monkeypatch，不调 API）。"""
import unittest

from bookrpg import llm
from bookrpg.engine import MAX_HISTORY_TURNS, Game

FAKE_BOOK = {
    "book": {"title": "测试书"},
    "world": {"setting": "测试世界", "rules": ["规则1"], "locations": [], "factions": []},
    "characters": [{"name": "主角", "role": "主角", "personality": "勇敢"}],
    "plot": {"outline": "大纲", "key_events": [], "themes": "", "original_ending": "结局"},
    "items": [],
    "lore_notes": "",
}


def make_game() -> Game:
    return Game(FAKE_BOOK, player="主角", player_desc="勇敢的测试者")


class TestSystemPrompt(unittest.TestCase):
    def test_output_format_at_end(self):
        sp = make_game()._system_prompt()
        # 【输出格式】段必须存在且在【最近对话】之后
        self.assertIn("【输出格式", sp)
        self.assertGreater(sp.find("【输出格式"), sp.find("【最近对话】"))

    def test_no_double_braces(self):
        sp = make_game()._system_prompt()
        tail = sp[sp.find("【输出格式"):]
        self.assertNotIn("{{", tail)
        self.assertNotIn("}}", tail)

    def test_rules_no_schema(self):
        sp = make_game()._system_prompt()
        head = sp[:sp.find("【最近对话】")]
        self.assertNotIn("narrative", head)  # JSON schema 已移到末尾

    def test_empty_history_placeholder(self):
        sp = make_game()._system_prompt()
        self.assertIn("（游戏刚开始）", sp)


class TestRequestConstruction(unittest.TestCase):
    def test_turn_request_shape(self):
        """回合请求：末尾追加 JSON 指令、max_tokens=4096、temperature=0.7。"""
        seen = {}

        def fake_cj(messages, **kw):
            seen["msgs"] = messages
            seen["kw"] = kw
            return {"narrative": "n", "options": ["a"], "state_changes": {},
                    "scene": "s", "game_over": None}

        orig = llm.chat_json
        llm.chat_json = fake_cj
        try:
            make_game()._request_json([{"role": "user", "content": "行动"}])
        finally:
            llm.chat_json = orig
        msgs = seen["msgs"]
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("JSON 对象", msgs[-1]["content"])
        self.assertEqual(seen["kw"]["max_tokens"], 4096)
        self.assertEqual(seen["kw"]["temperature"], 0.7)


class TestTurnLogic(unittest.TestCase):
    def test_step_updates_state_and_history(self):
        g = make_game()
        seq = [
            {"narrative": "开场", "options": ["a", "b"], "state_changes": {},
             "scene": "开场场景", "game_over": None},
            {"narrative": "你花了钱", "options": ["a"], "state_changes": {"金钱": -10},
             "scene": "集市", "game_over": None},
        ]
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: seq.pop(0)
        try:
            r1 = g.new_game()
            self.assertEqual(r1["scene"], "开场场景")
            r2 = g.step("买一把剑")
        finally:
            llm.chat_json = orig
        self.assertEqual(g.state.to_dict()["金钱"], 90)
        self.assertEqual(g.scene, "集市")
        self.assertEqual(len(g.history), 3)  # 开场叙述 + 玩家 + 叙述

    def test_game_over_terminates(self):
        g = make_game()
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {
            "narrative": "你倒下了", "options": [], "state_changes": {},
            "scene": "终局", "game_over": "主角战死，故事结束。"}
        try:
            r = g.new_game()
        finally:
            llm.chat_json = orig
        self.assertIsNotNone(g.game_over)
        self.assertIsNotNone(r["game_over"])
        self.assertIn("战死", g.game_over)

    def test_degradation_fallback(self):
        """模型不配合 JSON（JSONResponseError）→ 降级为纯文本叙述（跳过选项），不崩溃。"""
        g = make_game()
        orig_cj, orig_chat = llm.chat_json, llm.chat
        llm.chat_json = lambda msgs, **kw: (_ for _ in ()).throw(llm.JSONResponseError("模型不配合"))
        llm.chat = lambda msgs, **kw: "纯文本叙述内容"
        try:
            r = g.step("随便干点啥")
        finally:
            llm.chat_json, llm.chat = orig_cj, orig_chat
        self.assertIn("纯文本叙述", r["narrative"])
        self.assertEqual(r["options"], [])
        self.assertEqual(g.history[-1]["content"], "纯文本叙述内容")

    def test_api_failure_not_degraded(self):
        """API 故障（401/断网等 RuntimeError）必须穿透，不得降级成纯文本（避免误导日志+重复调用）。"""
        g = make_game()
        orig_cj = llm.chat_json
        llm.chat_json = lambda msgs, **kw: (_ for _ in ()).throw(
            RuntimeError("API Key 无效或余额不足（HTTP 401）"))
        try:
            with self.assertRaisesRegex(RuntimeError, "API Key 无效"):
                g.step("随便干点啥")
        finally:
            llm.chat_json = orig_cj

    def test_options_capped_and_typed(self):
        g = make_game()
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {
            "narrative": "n", "options": ["1", "2", "3", "4", "5"], "state_changes": {},
            "scene": "", "game_over": None}
        try:
            r = g.new_game()
        finally:
            llm.chat_json = orig
        self.assertLessEqual(len(r["options"]), 4)


class TestHistoryCompression(unittest.TestCase):
    """长局历史压缩：超过 MAX_HISTORY_TURNS 轮后 history 受限，summary 保留并渲染。"""

    TURN = {"narrative": "n", "options": ["a"], "state_changes": {}, "scene": "s",
            "game_over": None}

    def _run_turns(self, g: Game, n: int, summary_text: str = "（前情提要：主角此前经历了若干冒险。）"):
        orig_cj, orig_chat = llm.chat_json, llm.chat
        calls = {"summaries": 0}
        llm.chat_json = lambda msgs, **kw: dict(self.TURN)
        llm.chat = lambda msgs, **kw: (calls.__setitem__("summaries", calls["summaries"] + 1)
                                       or summary_text)
        try:
            g.new_game()
            for _ in range(n):
                g.step("继续前进")
        finally:
            llm.chat_json, llm.chat = orig_cj, orig_chat
        return calls["summaries"]

    def test_compressed_after_40_turns(self):
        """80 步后：历史轮数受限（≤41）、summary 非空、系统提示词渲染前情提要。"""
        g = make_game()
        n_sum = self._run_turns(g, 80)
        turns = sum(1 for m in g.history if m["role"] == "user")
        self.assertGreaterEqual(n_sum, 2)  # 41步、61步各压缩一次
        self.assertLessEqual(turns, MAX_HISTORY_TURNS + 1)
        self.assertIn("前情提要", g.summary)
        sp = g._system_prompt()
        self.assertIn("【前情提要】", sp)
        self.assertIn(g.summary[:20], sp)

    def test_no_compress_before_threshold(self):
        """阈值内不压缩：summary 为空、llm.chat 不被调用（只走 chat_json）。"""
        g = make_game()
        n_sum = self._run_turns(g, 30)
        self.assertEqual(n_sum, 0)
        self.assertEqual(g.summary, "")
        self.assertNotIn("【前情提要】", g._system_prompt())

    def test_compress_failure_degrades_gracefully(self):
        """总结调用失败：不抛异常、不阻塞，history 仍截断；已生成的旧提要保留。"""
        g = make_game()
        orig_cj, orig_chat = llm.chat_json, llm.chat
        llm.chat_json = lambda msgs, **kw: dict(self.TURN)
        # 前 2 次总结成功，之后抛错
        state = {"ok": 2}
        def flaky_chat(msgs, **kw):
            if state["ok"] > 0:
                state["ok"] -= 1
                return "（前情提要：稳定的第一版。）"
            raise RuntimeError("API 故障")
        llm.chat = flaky_chat
        try:
            g.new_game()
            for _ in range(90):
                g.step("继续前进")
        finally:
            llm.chat_json, llm.chat = orig_cj, orig_chat
        turns = sum(1 for m in g.history if m["role"] == "user")
        self.assertLessEqual(turns, MAX_HISTORY_TURNS + 1)  # 不因失败停止截断
        self.assertIn("稳定的第一版", g.summary)  # 旧提要保留


class TestOptionsFallback(unittest.TestCase):
    """模型漏 options（JSON 合法但选项空）→ 自动重试一次（实测存档1 场景）。"""

    TURN = {"narrative": "叙述", "options": ["a", "b"], "state_changes": {},
            "scene": "s", "game_over": None}

    def test_retry_when_options_empty(self):
        """step 首次返回空 options → 重试一次，用第二次结果，history 只记一次。"""
        g = make_game()
        seq = [
            dict(self.TURN),                                        # new_game
            {"narrative": "叙述A", "options": [], "state_changes": {},
             "scene": "s", "game_over": None},                      # step 首次：空选项
            {"narrative": "叙述B", "options": ["选项1", "选项2"], "state_changes": {},
             "scene": "s", "game_over": None},                      # step 重试
        ]
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: seq.pop(0)
        try:
            g.new_game()
            r = g.step("行动")
        finally:
            llm.chat_json = orig
        self.assertEqual(r["options"], ["选项1", "选项2"])
        self.assertEqual(g.history[-1]["content"], "叙述B")  # 叙述A 未入 history
        self.assertEqual(len(g.history), 3)  # 开局叙述 + 玩家 + 叙述B

    def test_no_retry_when_options_present(self):
        """正常返回选项 → 不重试（每回合恰好 1 次调用）。"""
        g = make_game()
        calls = []
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: calls.append(1) or dict(self.TURN)
        try:
            g.new_game()
            g.step("行动")
        finally:
            llm.chat_json = orig
        self.assertEqual(len(calls), 2)  # new_game + step 各一次

    def test_no_retry_on_degraded(self):
        """降级路径（_degraded=True，纯文本）→ 不重试。"""
        g = make_game()
        calls = []
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: calls.append(1) or {
            "narrative": "纯文本叙述", "options": [], "state_changes": {},
            "scene": "s", "game_over": None, "_degraded": True}
        try:
            g.new_game()
            r = g.step("行动")
        finally:
            llm.chat_json = orig
        self.assertEqual(len(calls), 2)  # 不额外重试
        self.assertEqual(r["options"], [])

    def test_retry_accepts_empty_second_result(self):
        """重试后仍空 → 接受空（不无限重试），不崩溃。"""
        g = make_game()
        calls = []
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: calls.append(1) or {
            "narrative": "叙述", "options": [], "state_changes": {},
            "scene": "s", "game_over": None}
        try:
            g.new_game()
            r = g.step("行动")
        finally:
            llm.chat_json = orig
        self.assertEqual(len(calls), 4)  # new_game(空→重试=2) + step(空→重试=2)
        self.assertEqual(r["options"], [])


class TestSanitizeChanges(unittest.TestCase):
    """编造人物拦截：state_changes 新增人物必须在角色档案/待揭示名单内。"""

    BOOK = {
        **FAKE_BOOK,
        "characters": [
            {"name": "药老（药尘）", "role": "配角", "relations": "萧炎之师"},
            {"name": "萧薰儿/古薰儿", "role": "配角", "relations": "青梅竹马"},
        ],
    }

    def make_game(self) -> Game:
        g = Game(self.BOOK, player="主角")
        g.reveal_pool["关系"] = {"萧玄": "萧家先祖"}
        g.state.data["关系"] = {"萧战": "父亲"}
        return g

    def test_fabricated_character_blocked(self):
        """编造人物（不在名单）→ 剔除，不固化进状态。"""
        g = self.make_game()
        changes = g._sanitize_state_changes({"关系": {"云韵": "云岚宗大弟子，见证退婚"}})
        self.assertEqual(changes["关系"], {})
        self.assertNotIn("云韵", g.state.to_dict()["关系"])

    def test_known_character_allowed(self):
        """名单内人物（角色档案/待揭示池）→ 正常登记。"""
        g = self.make_game()
        changes = g._sanitize_state_changes({"关系": {"药老（药尘）": "戒指中的灵魂", "萧玄": "萧家先祖"}})
        self.assertIn("药老（药尘）", changes["关系"])
        self.assertIn("萧玄", changes["关系"])

    def test_slash_alias_matches_roster(self):
        """斜杠别名（萧薰儿/古薰儿）→ 名单匹配通过。"""
        g = self.make_game()
        changes = g._sanitize_state_changes({"关系": {"古薰儿": "古族大小姐"}})
        self.assertIn("古薰儿", changes["关系"])

    def test_existing_key_allowed(self):
        """state 已有键（含别名重叠）→ 放行。"""
        g = self.make_game()
        changes = g._sanitize_state_changes({"关系": {"萧战": "父亲（更新）"}})
        self.assertIn("萧战", changes["关系"])

    def test_non_relation_changes_unaffected(self):
        """非关系变更（金钱等）→ 不受影响。"""
        g = self.make_game()
        changes = g._sanitize_state_changes({"金钱": -10, "关系": {"云韵": "编造"}})
        self.assertEqual(changes["金钱"], -10)
        self.assertEqual(changes["关系"], {})

    def test_end_to_end_apply_blocked(self):
        """step 全链路：编造人物被拦截，不进入 state。"""
        g = self.make_game()
        orig = llm.chat_json
        llm.chat_json = lambda msgs, **kw: {
            "narrative": "叙述", "options": ["a"], "state_changes": {"关系": {"云韵": "见证退婚"}},
            "scene": "s", "game_over": None}
        try:
            g.step("行动")
        finally:
            llm.chat_json = orig
        self.assertNotIn("云韵", g.state.to_dict()["关系"])
        self.assertIn("萧战", g.state.to_dict()["关系"])  # 原有关系保留


if __name__ == "__main__":
    unittest.main()
