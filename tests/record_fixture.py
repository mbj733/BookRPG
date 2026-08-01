"""录制脚本：跑一次真实游戏会话，把 LLM 调用落盘为 fixture。

用法（一次性，烧少量 token）：
    python tests/record_fixture.py
生成：tests/fixtures/game_session.jsonl
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bookrpg import llm, recorder, worldbook as wb
from bookrpg.engine import Game

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game_session.jsonl"
BOOK_FILE = Path(__file__).resolve().parent / "fixtures" / "books" / "星环守望者.book"

ACTIONS = [
    "我决定先查看归星室的仪表读数。",
    "我向烬老询问上一次星潮的记载。",
    "我尝试用灯语点亮塔顶信标。",
    "我向时鸢打听穹顶商盟的来意。",
    "我擦拭归星石，回想成为守灯人的那天。",
]


def main() -> None:
    if not BOOK_FILE.exists():
        print(f"缺少世界观包：{BOOK_FILE}（开源仓库自带 tests/fixtures/books/ 原创测试书）")
        return 1
    book = wb.load_worldbook(str(BOOK_FILE))
    game = Game(book, player="阿澈", player_desc="新一代守灯人",
                worldbook_file=BOOK_FILE.name)

    # 录制：真实调用 + 落盘
    llm.chat = recorder.record(llm.chat, str(FIXTURE))
    degrades: list[str] = []

    class Cap(io.StringIO):
        def write(self, s):
            if "[引擎]" in s:
                degrades.append(s.strip())
            return super().write(s)

    with redirect_stdout(Cap()):
        game.new_game()
        for act in ACTIONS:
            game.step(act)

    n = len(recorder.load_records(str(FIXTURE)))
    recorder.write_meta(str(FIXTURE), {"call_count": n, "degrade_count": len(degrades),
                                       "recorded_at": __import__("datetime").datetime.now().isoformat(timespec="seconds")})
    print(f"录制完成：{n} 次调用 → {FIXTURE}")
    print(f"录制期降级次数：{len(degrades)}")
    if degrades:
        for d in degrades:
            print(" ", d[:80])
    print("提示：录制期间若出现降级，回放测试会同样覆盖该路径（纠正重试也录入了）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
