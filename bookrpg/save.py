"""存档 / 读档：books/saves/{书名}/{存档名}.json"""
import json
from datetime import datetime
from pathlib import Path

from . import worldbook as wb
from .engine import Game


def save_game(game: Game, save_path: Path) -> None:
    """保存游戏。存档含：扮演设定、状态、历史、场景、进度、worldbook 文件名。"""
    data = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "player": game.player,
        "player_desc": game.player_desc,
        "state": game.state.to_dict(),
        "history": game.history,
        "summary": game.summary,      # 长局压缩前情提要（旧存档无此字段，读档默认空）
        "scene": game.scene,
        "game_over": game.game_over,
        "options": game.options,      # 读档后继续可选行动
        "worldbook_file": game.worldbook_file,
    }
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_game(save_path: Path, books_dir: Path) -> Game:
    """读取存档 → 恢复 Game（自动重载对应 .book 世界观包）。"""
    d = json.loads(save_path.read_text(encoding="utf-8"))
    worldbook_file = d.get("worldbook_file")
    if worldbook_file:
        wb_path = books_dir / worldbook_file
        book = wb.load_worldbook(wb_path) if wb_path.exists() else {}
    else:
        book = {}
    g = Game(
        book,
        player=d.get("player", "主角"),
        player_desc=d.get("player_desc", ""),
        initial_state=d.get("state"),
        worldbook_file=worldbook_file,
    )
    g.history = d.get("history", [])
    g.summary = d.get("summary", "")  # 旧存档无 summary 字段 → 空（不压缩不渲染）
    g.scene = d.get("scene", "")
    g.game_over = d.get("game_over")
    g.options = d.get("options", [])  # 旧存档无此字段 → 空列表
    return g
