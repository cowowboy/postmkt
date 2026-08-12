# src/build_screen.py — 選股管線（法人預估 EPS 初篩＋FactSet 詳情）
#
# 產出 data/screen/screen.json：以 TradingView scanner 批次初篩「明年度預估 EPS ≥ 門檻」
# 的上市/上櫃個股（免金鑰），再逐檔向 cnyes（鉅亨）FactSet 端點拉預估 EPS 年度分佈、
# 目標價、評等家數，最後併入 data/diag/diag.json 既有欄位（本益比/營收/法人）供前端排表。
#
# 資料源（皆免金鑰、帶普通 UA）：
#   1. TradingView scanner：POST https://scanner.tradingview.com/taiwan/scan
#      filter: earnings_per_share_forecast_next_fy >= --min-feps（伺服器端過濾，實測可用）
#      columns: name/description/close/earnings_per_share_forecast_next_fy/
#               price_target_average/recommendation_mark
#      symbol 格式：上市 TWSE:2330、上櫃 TPEX:5274
#   2. cnyes marketinfo（上市上櫃一律 TWS: 前綴）：
#      - estimateProfit?type=eps → 各年度 {feHigh,feLow,feMean,feMedian,numEst,rateDate}
#      - targetPrice            → 單一 dict（含 chName 中文名）
#      - factSetEstimate        → 評等家數分佈，平行陣列、rateDate 新到舊，取最新一期
#        鍵名對映（2026-08-12 實測）：feBuy→buy、feOver→overweight、feHold→hold、
#        feUnder→underweight、feSell→sell
#   3. data/diag/diag.json（本 repo diag 管線）：pe/yoy/mom/rvs/f5/t5（1200 檔外留 null）
#
# 節流與失敗處理：cnyes 每檔之間 sleep ≥ SLEEP_SEC 秒；單檔單端點失敗（非 200/JSON 壞）
# 記 warning、該欄位留 null、不中斷整體；「連續 ABORT_STREAK 檔三端點全失敗」或
# TradingView 初篩本身失敗才 abort——abort 時不寫檔，不覆蓋既有 screen.json。
#
# 用法：
#   python src/build_screen.py                      # 正式（免 token）
#   python src/build_screen.py --limit 5            # 除錯：只抓前 5 檔 cnyes 詳情
#   python src/build_screen.py --min-feps 30 --out /tmp/screen.json

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

try:  # Windows 本地終端 cp950 會把中文 print 成亂碼/報錯；Actions 上是 UTF-8 無感
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from fmclient import TAIPEI, taipei_today  # noqa: E402 — 同目錄共用模組

ROOT = Path(__file__).resolve().parent.parent  # repo 根（本檔在 src/ 下）
OUT_PATH = ROOT / "data" / "screen" / "screen.json"
DIAG_PATH = ROOT / "data" / "diag" / "diag.json"

TV_URL = "https://scanner.tradingview.com/taiwan/scan"
CNYES_BASE = "https://marketinfo.api.cnyes.com/mi/api/v1/financialIndicator"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MIN_FEPS = 20          # 明年度預估 EPS 初篩門檻（--min-feps 可覆蓋）
TV_RANGE = 800         # TradingView 取回上限（實測門檻 20 約 90 檔，留大緩衝）
SLEEP_SEC = 1.2        # cnyes 每檔之間節流秒數
ABORT_STREAK = 10      # 連續 N 檔 cnyes 三端點全失敗 → abort（不覆蓋舊檔）
DIAG_KEYS = ("pe", "yoy", "mom", "rvs", "f5", "t5")  # 從 diag.json 帶入的欄位


class ScreenAbort(RuntimeError):
    """整體中止（初篩失敗或 cnyes 連續全失敗）——呼叫端不得寫出 screen.json。"""


def r2(v):
    return None if v is None else round(float(v), 2)


# ---------- 1. TradingView 批次初篩 ----------

def tv_payload(min_feps: float) -> dict:
    return {
        "filter": [{"left": "earnings_per_share_forecast_next_fy", "operation": "egreater", "right": min_feps}],
        "columns": [
            "name", "description", "close",
            "earnings_per_share_forecast_next_fy", "price_target_average", "recommendation_mark",
        ],
        "sort": {"sortBy": "earnings_per_share_forecast_next_fy", "sortOrder": "desc"},
        "range": [0, TV_RANGE],
    }


def parse_tv(j: dict) -> list[dict]:
    """解析 scanner 回應 → 候選清單。symbol 'TWSE:2330'→mkt twse、'TPEX:5274'→tpex；
    交易所前綴不認得的略過（防指數/期貨等混入）。"""
    out = []
    for row in j.get("data") or []:
        sym = row.get("s") or ""
        exch, _, code = sym.partition(":")
        mkt = {"TWSE": "twse", "TPEX": "tpex"}.get(exch)
        if not mkt or not code:
            continue
        d = row.get("d") or []
        if len(d) < 6:
            continue
        name, desc, close, feps, tp, rec = d[:6]
        out.append({
            "code": code, "name": name or code, "desc": desc, "mkt": mkt,
            "px": r2(close),
            "tv": {"feps": r2(feps), "tp": r2(tp), "rec": r2(rec)},
        })
    return out


def fetch_tv(min_feps: float) -> list[dict]:
    try:
        r = requests.post(TV_URL, json=tv_payload(min_feps), headers=HEADERS, timeout=30)
        r.raise_for_status()
        cands = parse_tv(r.json())
    except Exception as e:  # noqa: BLE001 — 初篩失敗＝沒有宇宙，整體中止
        raise ScreenAbort(f"TradingView 初篩失敗: {e}") from e
    if not cands:
        raise ScreenAbort("TradingView 初篩回 0 檔（疑似 API 變動），中止不覆蓋舊檔")
    return cands


# ---------- 2. cnyes FactSet 詳情 ----------

def cnyes_get(path: str, code: str):
    """單端點 GET。成功回 data（dict/list），失敗（非 200/JSON 壞）印 warning 回 None。"""
    url = f"{CNYES_BASE}/{path}/TWS:{code}:STOCK"
    if path == "estimateProfit":
        url += "?type=eps"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j.get("statusCode") != 200:
            raise RuntimeError(f"statusCode={j.get('statusCode')}")
        return j.get("data")
    except Exception as e:  # noqa: BLE001 — 單端點失敗降級為該欄 null
        print(f"  ! cnyes {path} {code} 失敗（{e}），該欄位留 null")
        return None


def parse_est(data) -> dict | None:
    """estimateProfit?type=eps → {"2026": {mean,median,high,low,n,date}, ...}（年度字串鍵）。"""
    if not isinstance(data, list) or not data:
        return None
    est = {}
    for row in data:
        y = row.get("financialYear")
        if y is None:
            continue
        est[str(y)] = {
            "mean": r2(row.get("feMean")), "median": r2(row.get("feMedian")),
            "high": r2(row.get("feHigh")), "low": r2(row.get("feLow")),
            "n": row.get("numEst"), "date": row.get("rateDate"),
        }
    return est or None


def parse_tp(data) -> dict | None:
    """targetPrice → {median,high,low,n,date}。data 為單一 dict。"""
    if not isinstance(data, dict) or data.get("feMedian") is None:
        return None
    return {
        "median": r2(data.get("feMedian")), "high": r2(data.get("feHigh")),
        "low": r2(data.get("feLow")), "n": data.get("numEst"), "date": data.get("rateDate"),
    }


def parse_rating(data) -> dict | None:
    """factSetEstimate → 取最新一期評等家數。回應為平行陣列（rateDate epoch 秒、新到舊），
    以 rateDate 最大者為最新期（防陣列順序變動）。鍵名對映見檔頭註解。"""
    if not isinstance(data, dict):
        return None
    dates = data.get("rateDate")
    if not isinstance(dates, list) or not dates:
        return None
    i = max(range(len(dates)), key=lambda k: dates[k] or 0)

    def pick(key):
        arr = data.get(key)
        return arr[i] if isinstance(arr, list) and i < len(arr) else None

    rating = {
        "buy": pick("feBuy"), "overweight": pick("feOver"), "hold": pick("feHold"),
        "underweight": pick("feUnder"), "sell": pick("feSell"),
    }
    return None if all(v is None for v in rating.values()) else rating


def fetch_cnyes(code: str) -> tuple[dict, bool]:
    """單檔三端點。回 (欄位 dict, all_failed)——all_failed 供連續失敗 abort 判定。"""
    est_raw = cnyes_get("estimateProfit", code)
    tp_raw = cnyes_get("targetPrice", code)
    fact_raw = cnyes_get("factSetEstimate", code)
    ch_name = None
    for src in (tp_raw, fact_raw):
        if isinstance(src, dict) and src.get("chName"):
            ch_name = src["chName"]
            break
    fields = {
        "est": parse_est(est_raw), "tp": parse_tp(tp_raw), "rating": parse_rating(fact_raw),
        "ch_name": ch_name,
    }
    return fields, (est_raw is None and tp_raw is None and fact_raw is None)


# ---------- 3. diag 合併／排序 ----------

def load_diag_stocks(path: Path = DIAG_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("stocks") or {}
    except Exception as e:  # noqa: BLE001 — diag 缺檔只影響 diag 欄，不中止
        print(f"  ! 讀不到 {path}（{e}），diag 欄位全留 null")
        return {}


def diag_fields(stocks: dict, code: str) -> dict:
    row = stocks.get(code) or {}
    return {k: row.get(k) for k in DIAG_KEYS}


def sort_key(row: dict, next_year: str, this_year: str):
    """排序鍵：est 明年度 mean 降冪；缺明年度退用今年度，再退 TradingView feps，全缺沉底。"""
    est = row.get("est") or {}
    for y in (next_year, this_year):
        m = (est.get(y) or {}).get("mean")
        if m is not None:
            return m
    return row["tv"]["feps"] if row["tv"]["feps"] is not None else float("-inf")


# ---------- main ----------

def build(min_feps: float, limit: int | None) -> dict:
    cands = fetch_tv(min_feps)
    if limit:
        cands = cands[:limit]
    print(f"TradingView 初篩（feps>={min_feps}）：{len(cands)} 檔")

    diag_stocks = load_diag_stocks()
    rows, fail_streak, n_fail = [], 0, 0
    for i, c in enumerate(cands):
        if i:
            time.sleep(SLEEP_SEC)
        fields, all_failed = fetch_cnyes(c["code"])
        if all_failed:
            n_fail += 1
            fail_streak += 1
            if fail_streak >= ABORT_STREAK:
                raise ScreenAbort(f"cnyes 連續 {fail_streak} 檔全失敗，中止不覆蓋舊檔")
        else:
            fail_streak = 0
        rows.append({
            "code": c["code"],
            "name": fields["ch_name"] or diag_stocks.get(c["code"], {}).get("n") or c["desc"] or c["name"],
            "mkt": c["mkt"], "px": c["px"], "tv": c["tv"],
            "est": fields["est"], "tp": fields["tp"], "rating": fields["rating"],
            "diag": diag_fields(diag_stocks, c["code"]),
        })
        if (i + 1) % 20 == 0:
            print(f"  cnyes 進度 {i + 1}/{len(cands)}", flush=True)

    today = taipei_today()
    rows.sort(key=lambda r: sort_key(r, str(today.year + 1), str(today.year)), reverse=True)
    print(f"cnyes 完成：{len(rows)} 檔（全失敗 {n_fail} 檔）")
    return {
        "date": today.isoformat(),
        "generated_at": dt.datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "min_feps": min_feps,
        "n": len(rows),
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="選股管線：TradingView 初篩＋cnyes FactSet 詳情")
    ap.add_argument("--min-feps", type=float, default=MIN_FEPS, help="明年度預估 EPS 門檻")
    ap.add_argument("--limit", type=int, default=None, help="除錯用：限制 cnyes 抓取檔數")
    ap.add_argument("--out", default=str(OUT_PATH), help="輸出路徑")
    args = ap.parse_args(argv)

    t0 = time.time()
    try:
        payload = build(args.min_feps, args.limit)
    except ScreenAbort as e:
        print(f"!! abort：{e}")
        return 1  # 不寫檔＝既有 screen.json 原樣保留

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"寫出 {out}（n={payload['n']}，{time.time() - t0:.0f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
