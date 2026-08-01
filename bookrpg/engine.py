"""游戏引擎：回合循环、上下文管理、状态更新。

核心设计原则：每回合请求只带「世界观包 + 最近 8 轮对话 + 当前状态」，
绝不重读原文——省 token、响应快、行为稳定。
"""
import json

from . import llm
from .state import GameState

SYSTEM_TEMPLATE = """【你是这本小说的世界引擎，扮演这个世界本身。】请基于以下设定运行游戏。

世界设定（来自世界观包）：
{world}

角色档案（仅作背景知识，玩家扮演谁看下方扮演设定）：
{characters}

剧情大纲（玩家偏离时你可自由发挥，不强制跟随）：
{plot}

【扮演设定】
玩家扮演：{player}（{player_desc}）
当前状态：{state}

【规则】
1. 玩家可以自由输入任何行动，不限于你提供的选项——选项只是建议，玩家输入的任何内容（说话、观察、移动、做任何事）都是新的行动指令，你必须合理应对，允许剧情完全偏离原著
2. 结局条件自由：死亡、达成目标、重大抉择等都算结局，触发时 game_over 填结局描述文本
3. 保持角色人设一致，玩家扮演的角色不能 OOC
4. 状态变更必须与叙述一致（叙述里花了钱，state_changes 就要扣钱）
5. 涉及原著角色时，姓名必须严格取自【角色档案】名单，严禁改名、张冠李戴或编造（例如把纳兰嫣然写成林浅雪、把药老写成药谷都是错误的）。角色间关系以档案描述为准，关系不明时宁可模糊带过，也不要编造关系。
6. 每次玩家输入都必须产生新的剧情进展：推进事件、场景、对话或发现，严禁复述、重复或改述上一段叙述——即使玩家输入很简短或看似与当前场景无关，也要给出新的世界反应，而不是原地打转。

【最近对话】
{history}

【输出格式（必须严格遵守，这是最重要的要求）】
每次回复必须只返回一个 JSON 对象，不要输出任何其他内容（不要叙述、不要解释、不要代码块标记），结构固定为：
{{
  "narrative": "叙述文本：描写场景、NPC反应、事件推进，150~400字，用空行分成2~4个小段落（段落间留空行），不要挤成一大段",
  "options": ["选项1", "选项2", "选项3"],
  "state_changes": {{"金钱": -10, "好感_张三": 5}},
  "initial_state": null,
  "scene": "当前场景摘要（10字内）",
  "game_over": null
}}
说明：
- options 必须方向分歧：2~4 个，覆盖不同立场/不同风险/不同收益（如谨慎行事 vs 冒险激进 vs 另辟蹊径 vs 静观其变），让玩家有真正的抉择感。严禁同质化——不要写"去做X"和"小心地去做X"这种只差一个形容词的选项。至少一个选项有明显风险或代价。
- state_changes 只在叙述中明确出现金钱/生命/资源变化行为（购买、奖励、损失、受伤等）时才填对应增减；否则必须为 {{}}。禁止无中生有地扣除或增加属性——例如玩家只是对话、观察、移动，就绝不能扣钱。
- 玩家获得/失去重要物品、功法、装备、关系时，通过 state_changes 新增或更新对应属性（如 {{"背包": ["低级储物袋"], "功法": ["黄阶功法《焚决》"]}}）；失去/用完时将该属性设为 null（null=移除，状态栏将不再显示该属性）。属性在玩家拥有前不要出现在状态里。
- initial_state 仅在开局回合填写（其他回合必须为 null）：根据本书的世界观设计 4~8 个关键状态属性及初始值，不要套用固定模板。例：斗气玄幻书（生命、金钱、斗气、境界、灵魂感知、声望）；科幻悬疑书（生命、金钱、线索进度、信任度、体力）；宫廷权谋书（生命、金钱、权势、威望、人脉）。属性名用中文，初始值贴合原著开局设定。"""

# 开局背景导入指令：先建立世界观认知，再引到起点（不直接叙述事件）
OPENING_INSTRUCTION = (
    "（游戏开始）请先向玩家介绍这个世界的大致背景，约 500 字："
    "时代与格局、主要势力、玩家扮演角色的身份与处境、当前局势。"
    "让玩家先建立对世界的认知，然后自然引出此刻的起点场景，"
    "并以一个悬念或选择点收尾。不要直接叙述重大事件的发生。"
    "背景中提及的任何角色姓名、关系必须严格取自【角色档案】名单，"
    "禁止改名或编造；关系不明时宁可模糊带过。"
    "同时根据本书世界观在 initial_state 中设计 4~8 个关键状态属性及初始值"
    "（斗气玄幻：生命/金钱/斗气/境界等；科幻：生命/金钱/线索进度/信任度等），"
    "不要用固定模板。"
)

# 命中任一关键词 → 该回合启用深度思考（关键/复杂内容）
DEEP_THINKING_KEYWORDS = [
    "决定", "抉择", "决战", "开战", "背叛", "投靠", "刺杀", "挑战",
    "突破", "拜师", "求婚", "告白", "揭穿", "坦白", "牺牲", "逃离",
    "逃跑", "生死", "交易", "结盟", "反目", "质问", "逼问", "自尽",
    "动手", "摊牌", "孤注一掷",
]

# 长局历史压缩：超过 MAX_HISTORY_TURNS 轮（按玩家输入计数）时，
# 把最早的轮次用 LLM 浓缩成一条「前情提要」存进 self.summary，
# history 只保留最近 COMPRESS_KEEP_TURNS 轮——控制存档体积与内存，
# 且与"绝不重读原文、只带最近 8 轮"的上下文原则一致。
MAX_HISTORY_TURNS = 40
COMPRESS_KEEP_TURNS = 20

COMPRESS_PROMPT = """你是游戏前情提要摘要器。下面是玩家此前经历的部分对话轮次（玩家行动与叙述）。
请用 200~400 字中文按时间顺序浓缩成「前情提要」，用于后续回合的背景回顾。
严格要求：
1. 只总结轮次中实际发生的内容，禁止添加轮次中不存在的事件、人物、物品或设定
2. 保留：玩家身份、关键决定、重要遭遇、状态变化、未解悬念
3. 用第三人称叙述，不要评价
只输出提要文本，不要输出任何其他内容。"""


class Game:
    def __init__(self, worldbook: dict, player: str = "主角", player_desc: str = "自由行动的主角",
                 initial_state: dict | None = None, worldbook_file: str | None = None):
        self.worldbook = worldbook
        self.player = player
        self.player_desc = player_desc
        self.state = GameState(initial_state)
        self.history: list[dict] = []   # [{"role": "user"/"assistant", "content": ...}]
        self.summary: str = ""          # 长局压缩后的「前情提要」（旧存档无此字段 → 空）
        self.scene = ""
        self.game_over = None
        self.options: list[str] = []    # 当前回合的可选行动（存档/读档用）
        self.worldbook_file = worldbook_file  # 存档恢复用
        self.stream_cb = None           # 流式回调（由界面设置，worker 线程调用）

    # ---------- 公开接口 ----------

    def new_game(self) -> dict:
        """生成开场（历史为空）：先介绍世界背景，再引到起点。"""
        self.history = []
        return self._step(opening=True)

    def step(self, player_input: str) -> dict:
        """玩家行动 → 返回 {narrative, options, state, scene, game_over}。

        关键/复杂行动（命中 DEEP_THINKING_KEYWORDS）启用深度思考，
        日常行动直接快速响应。
        """
        self.history.append({"role": "user", "content": player_input})
        deep = any(kw in player_input for kw in DEEP_THINKING_KEYWORDS)
        return self._step(deep=deep)

    # ---------- 内部 ----------

    def _system_prompt(self) -> str:
        w = self.worldbook
        recent = "\n".join(
            f"{'玩家' if m['role'] == 'user' else '叙述'}: {m['content'][:500]}"
            for m in self.history[-8:]
        )
        if self.summary:
            # 有前情提要时，把它放在最近对话之前作背景回顾
            history = f"【前情提要】\n{self.summary}\n\n{recent or '（游戏刚开始）'}"
        else:
            history = recent or "（游戏刚开始）"
        return SYSTEM_TEMPLATE.format(
            world=json.dumps(w.get("world", {}), ensure_ascii=False),
            characters=json.dumps(w.get("characters", []), ensure_ascii=False),
            plot=json.dumps(w.get("plot", {}), ensure_ascii=False),
            player=self.player,
            player_desc=self.player_desc,
            state=json.dumps(self.state.to_dict(), ensure_ascii=False),
            history=history or "（游戏刚开始）",
        )

    def _step(self, opening: bool = False, deep: bool = False) -> dict:
        messages = [{"role": "system", "content": self._system_prompt()}]
        for m in self.history:
            messages.append({"role": m["role"], "content": m["content"]})

        result = self._request_json(messages, opening=opening, deep=deep)

        narrative = str(result.get("narrative", "")).strip()
        options = result.get("options") or []
        if not isinstance(options, list):
            options = [str(options)]
        options = [str(o) for o in options][:4]
        self.options = options  # 持久化供存档/读档
        scene = str(result.get("scene", "")).strip()
        game_over = result.get("game_over") or None

        self.state.apply(result.get("state_changes") or {})
        if opening:
            # 开局状态：优先用导入时生成的角色面板模板，其次用模型 initial_state 兜底
            tmpl = (self.worldbook.get("state_template") or {}).get("属性")
            if isinstance(tmpl, dict) and tmpl:
                self.state = GameState(tmpl)
            elif isinstance(result.get("initial_state"), dict):
                self.state = GameState(result["initial_state"])
        if scene:
            self.scene = scene
        if game_over:
            self.game_over = str(game_over)

        self.history.append({"role": "assistant", "content": narrative})
        self._maybe_compress()
        return {
            "narrative": narrative,
            "options": options,
            "state": self.state.to_dict(),
            "scene": self.scene,
            "game_over": self.game_over,
        }

    # ---------- 长局历史压缩 ----------

    def _maybe_compress(self) -> None:
        """超过 MAX_HISTORY_TURNS 轮时，把最早轮次浓缩进 summary，只留最近窗口。

        总结调用失败不阻塞游戏：保留旧提要、照常截断（summary 为空时直接丢弃）。
        """
        turns = sum(1 for m in self.history if m["role"] == "user")
        if turns <= MAX_HISTORY_TURNS:
            return
        keep = COMPRESS_KEEP_TURNS * 2
        dropped, rest = self.history[:-keep], self.history[-keep:]
        new_summary = self._compress_history(dropped)
        if new_summary.strip():
            self.summary = new_summary.strip()
        self.history = rest
        print(f"[引擎] 历史压缩：超过 {MAX_HISTORY_TURNS} 轮，"
              f"丢弃 {len(dropped)} 条消息，保留最近 {len(rest)} 条")

    def _compress_history(self, dropped: list[dict]) -> str:
        """把被丢弃的轮次（+旧提要）浓缩成一条前情提要。失败返回旧提要/空串。"""
        lines = []
        for m in dropped:
            who = "玩家" if m["role"] == "user" else "叙述"
            lines.append(f"{who}：{m['content'][:200]}")
        parts = []
        if self.summary:
            parts.append(f"（此前的提要）\n{self.summary}")
        parts.append("\n".join(lines))
        try:
            text = llm.chat(
                [{"role": "system", "content": COMPRESS_PROMPT},
                 {"role": "user", "content": "\n\n".join(parts)}],
                temperature=0.3, max_tokens=800, thinking=False,
            )
            return str(text or "").strip()
        except RuntimeError as e:
            print(f"[引擎] 历史压缩失败，保留旧提要（{e}）")
            return self.summary

    def _request_json(self, messages: list[dict], opening: bool = False,
                      deep: bool = False) -> dict:
        """请求 JSON；模型不配合（连续非 JSON）时降级为纯文本叙述（跳过选项）。

        对应制作流程 §5 风险表："解析失败自动重试；仍失败时降级为纯文本叙述"。
        末尾追加一条 user 指令消息——reasoning 模型对最后一条消息的遵从度最高。
        opening=True 时先追加开局背景导入指令；deep=True 时启用深度思考，
        否则禁用思考（响应更快，适合开局与日常推进）。
        """
        prompt = list(messages)
        if opening:
            prompt.append({"role": "user", "content": OPENING_INSTRUCTION})
        prompt.append({"role": "user", "content":
                       "请只返回一个 JSON 对象作为你的回复，不要输出任何其他内容。"
                       "注意：除非叙述中明确发生了消费/获得/受伤等行为，state_changes 必须为 {}。"
                       "根据玩家最新输入推进剧情，叙述必须与上一段明显不同，严禁复述上一段内容。"})
        try:
            # max_tokens 是 reasoning 模型"推理+输出"的总预算，必须给足：
            # 1500 会被推理吃光导致 JSON 截断/空内容（实测 3 回合降级 2 次）
            return llm.chat_json(prompt, temperature=0.7, max_tokens=4096,
                                 thinking=deep, stream_cb=self.stream_cb)
        except llm.JSONResponseError as e:
            print(f"[引擎] 模型未返回 JSON，降级为纯文本叙述（{e}）")
            text = llm.chat(prompt, temperature=0.7, max_tokens=4096,
                            thinking=deep, stream_cb=self.stream_cb)
            return {
                "narrative": text.strip() or "（叙述缺失，世界陷入沉寂。）",
                "options": [],
                "state_changes": {},
                "scene": "",
                "game_over": None,
            }
