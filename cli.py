"""CLI 入口（Phase 1 核心闭环）。

用法：
  python cli.py new <书路径> [--story 篇名关键词]
                              # 导入新书 → 生成世界观包 → 创建游戏
                              # EPUB 合集可用 --story 只通读单篇
  python cli.py list           # 列出书库
  python cli.py load <存档名>  # 读取存档继续玩
  python cli.py help           # 帮助

游玩循环内命令：
  save [存档名]   存档（默认 auto-{时间}）
  state           查看当前状态
  quit            退出（不自动存档）
"""
import sys
from pathlib import Path

from bookrpg import config, worldbook as wb
from bookrpg.engine import Game
from bookrpg.save import load_game, save_game

PROJECT_ROOT = Path(__file__).resolve().parent
BOOKS_DIR = PROJECT_ROOT / "books"
SAVES_ROOT = BOOKS_DIR / "saves"


# ---------- 命令 ----------

def cmd_list() -> None:
    books = sorted(BOOKS_DIR.glob("*.book"))
    if not books:
        print("书库为空。用 `python cli.py new <书路径>` 导入。")
        return
    print("书库：")
    for i, b in enumerate(books, 1):
        print(f"  {i}. {b.stem}")
    # 存档列表
    if SAVES_ROOT.exists():
        saves = sorted(SAVES_ROOT.glob("*/*.json"))
        if saves:
            print("\n存档：")
            for s in saves:
                print(f"  - {s.parent.name}/{s.stem}")


def cmd_new(book_path: str | None, story: str | None = None) -> None:
    if not book_path:
        print("用法：python cli.py new <书路径> [--story 篇名关键词]")
        print("      EPUB 合集可用 --story 只通读单篇，如 --story 你一生的故事"); return
    src = Path(book_path)
    if not src.exists():
        print(f"文件不存在：{src}"); return
    cfg = config.load()
    config.check(cfg)

    # 1) 通读生成世界观包
    print(f"导入书籍：{src}")
    book = wb.build_worldbook(str(src), story=story, cache_dir=BOOKS_DIR / ".cache")
    book_file = wb.save_worldbook(book, BOOKS_DIR)
    print(f"世界观包已生成：{book_file}")

    # 2) 选择扮演角色
    player, desc = _choose_player(book)
    if player is None:
        print("已取消。"); return

    # 3) 开始游戏
    game = Game(book, player=player, player_desc=desc, worldbook_file=book_file.name)
    _play(game)


def cmd_load(save_name: str | None) -> None:
    if not save_name:
        print("用法：python cli.py load <存档名>"); return
    hit = _find_save(save_name)
    if not hit:
        print(f"找不到存档：{save_name}（先 `python cli.py list` 看有哪些）"); return
    game = load_game(hit, BOOKS_DIR)
    if not game.worldbook:
        print(f"警告：世界观包文件缺失（{game.worldbook_file}），只能基于已有历史继续。")
    print(f"读取存档：{hit}（{game.player}）\n")
    _play(game)


# ---------- 交互 ----------

def _choose_player(book: dict) -> tuple[str | None, str]:
    chars = book.get("characters", [])
    print("\n选择扮演角色：")
    if chars:
        for i, c in enumerate(chars, 1):
            name = c.get("name", f"角色{i}")
            role = c.get("role", "")
            print(f"  {i}. {name}（{role}）—— {c.get('personality', '')[:30]}")
        print("  0. 自捏角色")
        choice = input("> ").strip()
        if choice == "0":
            return _ask_custom()
        if choice.isdigit() and 1 <= int(choice) <= len(chars):
            c = chars[int(choice) - 1]
            return c.get("name", "主角"), c.get("personality", "自由行动的主角")[:60]
        print("输入无效，改为自捏角色。")
    return _ask_custom()


def _ask_custom() -> tuple[str, str]:
    name = input("角色名字（回车=主角）> ").strip() or "主角"
    desc = input("一句话性格（回车=自由行动的主角）> ").strip() or "自由行动的主角"
    return name, desc


def _play(game: Game) -> None:
    print("\n" + "═" * 56)
    print("游戏开始！输入行动文字自由游玩；输入 `save` 存档、`state` 看状态、`quit` 退出。")
    print("═" * 56)
    last_options: list[str] = []
    while not game.game_over:
        if not game.history:
            # 开场
            result = game.new_game()
        else:
            action = input("\n> ").strip()
            if action.lower() == "quit":
                _prompt_save(game)
                return
            if action.lower() == "state":
                print("状态：", game.state.to_dict())
                continue
            if action.lower().startswith("save"):
                parts = action.split(maxsplit=1)
                name = parts[1] if len(parts) > 1 else f"auto-{_ts()}"
                _do_save(game, name)
                continue
            if action.isdigit() and last_options and 1 <= int(action) <= len(last_options):
                action = last_options[int(action) - 1]
            result = game.step(action)
        _render(result, game)
        last_options = result["options"]
        if game.game_over:
            print("\n【结局】" + game.game_over)
            _prompt_save(game)
            return


def _render(result: dict, game: Game) -> None:
    print("\n" + "─" * 56)
    if result["scene"]:
        print(f"[场景] {result['scene']}")
    print(result["narrative"])
    if result["options"]:
        print("\n可行动作：")
        for i, o in enumerate(result["options"], 1):
            print(f"  {i}. {o}")
    print(f"\n状态：{game.state.to_dict()}")


def _do_save(game: Game, name: str) -> None:
    book_dir = SAVES_ROOT / (game.worldbook_file or "未知").removesuffix(".book")
    sp = book_dir / f"{name}.json"
    save_game(game, sp)
    print(f"已存档：{sp}")


def _prompt_save(game: Game) -> None:
    ans = input("要存档吗？(y/n) > ").strip().lower()
    if ans in ("y", "yes"):
        _do_save(game, f"manual-{_ts()}")


def _find_save(save_name: str) -> Path | None:
    if not SAVES_ROOT.exists():
        return None
    # 支持 "书名/存档名" 与 "存档名" 两种写法
    if "/" in save_name or "\\" in save_name:
        p = SAVES_ROOT / f"{save_name}.json"
        return p if p.exists() else None
    hits = list(SAVES_ROOT.glob(f"*/{save_name}.json"))
    return hits[0] if hits else None


def _ts() -> str:
    import time
    return time.strftime("%m%d_%H%M%S")


def print_help() -> None:
    print(__doc__)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("help", "-h", "--help"):
        print_help(); return
    cmd = args[0]
    if cmd == "list":
        cmd_list()
    elif cmd == "new":
        # 解析 --story 关键词
        story = None
        rest = args[1:]
        if "--story" in rest:
            i = rest.index("--story")
            if i + 1 < len(rest):
                story = rest[i + 1]
                rest = rest[:i] + rest[i + 2:]
        cmd_new(rest[0] if rest else None, story=story)
    elif cmd == "load":
        cmd_load(args[1] if len(args) > 1 else None)
    else:
        print(f"未知命令：{cmd}"); print_help()


if __name__ == "__main__":
    main()
