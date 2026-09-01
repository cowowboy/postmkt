"""單一換址點(Python 側)。

換帳號或換託管只改這裡;CI 上也可以用環境變數覆寫,不必改碼:

    RAW_ORG=https://raw.githubusercontent.com/<你的帳號>
    WORKER_BASE=https://taiwan-flow-v2.<你的帳號>.workers.dev

本 repo 有三個換址點,搬家時要一起動:
  1. src/sites.py   這裡(Python 管線)
  2. index.html     SITE 物件(前端 JS)
  3. index.html     第 10 行的 CSP connect-src 白名單、以及「回 Hub」的靜態連結
     —— meta 標籤與純 HTML 沒辦法插值,只能各寫一份

三者由 tests/test_sites_consistency.py 強制一致:CSP 沒跟著改,瀏覽器會**靜默**
擋掉對新網域的請求(console 有錯但頁面只是空白),那是最難查的一種壞法。

src/*.py 以 `python src/xxx.py` 從 repo 根執行,sys.path[0] 就是 src/;
repo 根的 build_summary.py 則需自行把 src/ 加進 sys.path(見該檔)。
"""
from __future__ import annotations

import os

RAW_ORG = os.environ.get("RAW_ORG", "https://raw.githubusercontent.com/shihpc")
WORKER = os.environ.get("WORKER_BASE", "https://taiwan-flow-v2.shihpc.workers.dev")
HUB = os.environ.get("HUB_BASE", "https://shihpc.github.io")


def raw_base(repo: str) -> str:
    """<RAW_ORG>/<repo>/main"""
    return f"{RAW_ORG}/{repo}/main"


def raw(repo: str, path: str) -> str:
    """<RAW_ORG>/<repo>/main/<path>"""
    return f"{raw_base(repo)}/{path}"
