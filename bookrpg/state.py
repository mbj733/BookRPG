"""状态系统：动态属性 JSON，增量更新，序列化。

属性不是写死的（生命/金钱/好感度等），模型按需创建，界面自动渲染。
state_changes 语义：数值相加（-10 表示扣 10），字符串覆盖。
"""
from __future__ import annotations

import json


class GameState:
    def __init__(self, initial: dict | None = None):
        self.data = dict(initial or {"生命": 100, "金钱": 100})

    def apply(self, changes: dict | None) -> None:
        """应用模型返回的 state_changes。

        语义：数值相加（-10 表示扣 10）；字符串覆盖；列表/字典覆盖；
        None 表示移除该属性（状态栏将不再显示，如"背包用完"）。
        """
        if not changes:
            return
        for k, v in changes.items():
            if v is None:
                self.data.pop(k, None)
            elif k in self.data and isinstance(self.data[k], (int, float)) and isinstance(v, (int, float)):
                self.data[k] = round(self.data[k] + v, 2)
            else:
                self.data[k] = v

    def to_dict(self) -> dict:
        return dict(self.data)

    def __repr__(self) -> str:
        return f"GameState({json.dumps(self.data, ensure_ascii=False)})"
