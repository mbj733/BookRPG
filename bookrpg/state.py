"""状态系统：动态属性 JSON，增量更新，序列化。

属性不是写死的（生命/金钱/好感度等），模型按需创建，界面自动渲染。
state_changes 语义：数值相加（-10 表示扣 10），字符串覆盖，
字典浅合并（只更新/新增键，不整体覆盖——登记一个新关系不该冲掉其他关系），
列表整体覆盖（背包语义），None 删除属性。

嵌套对象一律深拷贝：开局模板/存档 dict 不得与内部状态共享引用
（否则引擎原地改状态会污染 worldbook 模板与存档数据）。
"""
from __future__ import annotations

import copy
import json


class GameState:
    def __init__(self, initial: dict | None = None):
        self.data = copy.deepcopy(initial) if initial else {"生命": 100, "金钱": 100}

    def apply(self, changes: dict | None) -> None:
        """应用模型返回的 state_changes。

        语义：数值相加（-10 表示扣 10）；字符串覆盖；列表整体覆盖；
        字典浅合并（新增/更新键，保留未提及键）；None 表示移除该属性。
        """
        if not changes:
            return
        for k, v in changes.items():
            if v is None:
                self.data.pop(k, None)
            elif k in self.data and isinstance(self.data[k], (int, float)) and isinstance(v, (int, float)):
                self.data[k] = round(self.data[k] + v, 2)
            elif isinstance(self.data.get(k), dict) and isinstance(v, dict):
                # 字典增量合并：只更新/新增键，不整体覆盖（避免丢掉其他关系/条目）
                self.data[k].update(copy.deepcopy(v))
            else:
                self.data[k] = copy.deepcopy(v)

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)

    def __repr__(self) -> str:
        return f"GameState({json.dumps(self.data, ensure_ascii=False)})"
