"""测试包：保证 tests/ 下模块能 import bookrpg。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
