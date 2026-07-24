# 讓測試可直接 import 根目錄（build_postmkt/build_summary）與 src/（fmclient 等）的模組
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
