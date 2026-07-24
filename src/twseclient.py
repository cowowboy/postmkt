# twseclient.py — TWSE 公開端點共用 client（全域節流鎖＋瀏覽器 headers）
#
# 教訓（2026-07-19，README「已知教訓」）：連續快速打 TWSE 端點約 6 次後會被 IP 限流，
# 回應變成非 JSON 空內容且不會自動解除。所以所有 TWSE HTTP 呼叫一律走 throttled_get()：
# 全域鎖序列化＋兩次請求至少間隔 THROTTLE_SECONDS 秒。務必保留，不要為了「加速」拿掉。
# 2026-07-24 由 build_mktbal.py 抽出供 build_postmkt.py 共用；節流秒數 env TWSE_THROTTLE
# 可調（沿用舊名 MKTBAL_TWSE_THROTTLE 作後備，向後相容）。
from __future__ import annotations

import os
import threading
import time

import requests

TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.twse.com.tw/zh/",
    "X-Requested-With": "XMLHttpRequest",
}
THROTTLE_SECONDS = float(os.environ.get("TWSE_THROTTLE",
                                        os.environ.get("MKTBAL_TWSE_THROTTLE", "4")))
RETRY_BACKOFFS = [2, 5, 10, 20]  # 秒；暫時性錯誤（含空回應）的重試等待序列（呼叫端自迴圈）

_lock = threading.Lock()
_last_request_ts = [0.0]


def resolve_cols(j: dict, spec: dict) -> dict:
    """依欄名關鍵字在 TWSE 回應的 fields metadata 中定位各欄 index，TWSE 調整欄位順序時
    仍能對到正確欄；fields 缺失或找不到時退回 spec 內建的固定 index（歷史行為）。
    spec = {name: (fallback_idx, include_keywords, exclude_keywords)}。"""
    fields = j.get("fields") or []
    out = {}
    for name, (fb, inc, exc) in spec.items():
        idx = next((i for i, f in enumerate(fields)
                    if all(k in str(f) for k in inc) and not any(k in str(f) for k in exc)), None)
        out[name] = idx if idx is not None else fb
    return out


def throttled_get(url: str, params: dict, timeout: int = 30) -> requests.Response:
    """所有 TWSE 請求都經過這裡，用全域鎖序列化＋節流，避免併發/連發觸發 IP 限流。"""
    with _lock:
        wait = THROTTLE_SECONDS - (time.monotonic() - _last_request_ts[0])
        if wait > 0:
            time.sleep(wait)
        try:
            return requests.get(url, params=params, timeout=timeout, headers=TWSE_HEADERS)
        finally:
            _last_request_ts[0] = time.monotonic()
