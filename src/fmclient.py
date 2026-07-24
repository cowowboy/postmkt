# fmclient.py — FinMind 共用 client＋token/台北時區工具
#
# 2026-07-24 由四支管線（build_postmkt/build_summary/build_diag/build_mktbal）抽出：
# 原本 api_get 有四份實作、重試策略互異（postmkt 甚至零重試），token() 四份、
# 台北時區常數三份。統一採原 build_diag.py 版重試策略（四支中最完整）：
# 失敗重試、402/429 限流先等 RATE_WAIT 秒再試、其他錯誤等 3 秒；重試耗盡 raise，
# 由呼叫端決定要不要吞（吞掉降級 vs 中止管線的語意留在各管線）。
#
# 匯入方式：src/ 內的管線直接 `from fmclient import ...`；根目錄管線先
# `sys.path.insert(0, str(ROOT / "src"))` 再 import（無 package 安裝、維持零依賴部署）。
from __future__ import annotations

import datetime as dt
import os
import time

import requests

BASE = "https://api.finmindtrade.com/api/v4/data"
TAIPEI = dt.timezone(dt.timedelta(hours=8))
RATE_WAIT = 65  # 402/429 限流的等待秒數（略大於 FinMind 一分鐘額度窗）


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI)


def taipei_today() -> dt.date:
    return taipei_now().date()


def token(required: bool = False) -> str:
    t = os.environ.get("FINMIND_TOKEN", "").strip()
    if required and not t:
        raise RuntimeError("找不到環境變數 FINMIND_TOKEN")
    return t


def api_get(dataset: str, url: str = BASE, retries: int = 2, **params) -> list:
    """通用 FinMind 查詢，回傳 data list。共 retries 次嘗試；
    重試耗盡 raise RuntimeError。url 可換端點（如 taiwan_stock_trading_daily_report）。"""
    q = dict(params, dataset=dataset)
    t = token()
    if t:
        q["token"] = t
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=q, timeout=60)
            if r.status_code in (402, 429):
                raise RuntimeError(f"{dataset}: rate limited (HTTP {r.status_code})")
            r.raise_for_status()
            j = r.json()
            if j.get("status") not in (200, None):
                raise RuntimeError(f"{dataset}: {j.get('msg')}")
            return j.get("data") or []
        except Exception as e:  # noqa: BLE001 — 統一重試
            last = e
            if attempt < retries:
                wait = RATE_WAIT if "rate limited" in str(e) else 3
                print(f"  ! {dataset} 失敗（{e}），{wait}s 後重試", flush=True)
                time.sleep(wait)
    raise RuntimeError(f"{dataset}: 重試後仍失敗: {last}")
