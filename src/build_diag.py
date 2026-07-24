# src/build_diag.py — 持股診斷素材庫管線（子期 1）
#
# 產出 data/diag/diag.json：全市場日均成交值前 ~1200 檔（上市＋上櫃）的
# 價量/籌碼/題材欄位＋單一 market 市場區塊，供前端「持股診斷」tab 逐檔評燈號。
# 素材庫是全市場通用資料，不含任何使用者持股資訊（持股只存使用者瀏覽器 localStorage）。
#
# 資料源（各欄位詳見 build_stock_fields()/build_market() 內逐欄註解）：
#   - FinMind TaiwanStockPrice                          價量（近 90 交易日、含 TAIEX 指數）
#   - FinMind TaiwanStockInstitutionalInvestorsBuySell  外資/投信買賣超（連續天數、近5日）
#   - FinMind TaiwanStockMarginPurchaseShortSale        融資餘額 5 日增減
#   - FinMind TaiwanDailyShortSaleBalances              借券賣出餘額 5 日變化
#   - FinMind TaiwanStockHoldingSharesPer               千張大戶持股比（週資料、快取輪替刷新）
#   - data/postmkt.json（本 repo 既有管線）              券資比、當沖量
#   - taiwan-flow-live-v2 raw：classify.json（次產業/產業鏈）、morning.json（連湧清單、
#     台指期夜盤 gap）、us.json（美股摘要）
#
# API 用量控制：價量/法人/融資/借券走 data/diag/cache.json 增量快取（每晚各 1-3 呼叫；
# 首跑回補一次較多）；千張大戶只能單檔查，採每晚上限 HOLD_CAP 檔輪替刷新（週資料）。
# 失敗處理：每個呼叫重試一次（限流多等 65s）；單一資料源失敗只把該來源欄位標 null，
# 不整批失敗（價量是宇宙基礎，快取也沒有時才會退出）。
# 金鑰：只讀環境變數 FINMIND_TOKEN（Actions secret 帶入）；--sample 模式允許無 token
# 以匿名額度少量代號空跑驗證流程。
#
# 用法：
#   python src/build_diag.py                    # 正式（需 FINMIND_TOKEN）
#   python src/build_diag.py --sample           # 本地驗證（預設 4 檔、可無 token）
#   python src/build_diag.py --sample 2330,2603 --out /tmp/diag.json

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

from fmclient import api_get, taipei_today, token  # noqa: E402 — 同目錄共用模組

ROOT = Path(__file__).resolve().parent.parent  # repo 根（本檔在 src/ 下）
V2_RAW = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data"

OUT_PATH = ROOT / "data" / "diag" / "diag.json"
CACHE_PATH = ROOT / "data" / "diag" / "cache.json"
PM_PATH = ROOT / "data" / "postmkt.json"

PRICE_DAYS = 66        # 快取保留交易日數（MA60/60日高需 60＋前日緩衝）
INST_DAYS = 20         # 法人買賣超保留交易日數（連買天數上限）
BAL_DAYS = 7           # 融資/借券餘額保留交易日數（5 日變化需 6）
UNIVERSE_N = 1200      # 輸出宇宙：20日日均成交值前 N 檔
CACHE_N = 2000         # 快取多留一層緩衝，宇宙邊緣進出不掉歷史
HOLD_CAP = 250         # 千張大戶每晚最多刷新檔數（週資料，一週內輪完全宇宙）
HOLD_STALE_DAYS = 6    # 千張大戶超過 N 天沒查過才重查
BACKFILL_DAYS = 100    # 首跑（無快取）價量回補日曆天數
REV_MONTHS = 26        # 月營收保留月數（YoY 連續成長/衰退最多可判 ~14 個月）
VAL_CAP = 100          # 估值/除權息（PER 3年百分位＋股利公告）每晚輪替刷新檔數
VAL_STALE_DAYS = 14    # 估值/除權息超過 N 天沒查過才重查
SAMPLE_CODES = ["2330", "2317", "2603", "3231"]

# 回補窗（日曆日）——full 與 --sample 兩條路徑共用同一組常數，避免兩邊各寫一份
# 數字後漂移、讓本地 --sample 驗證失去代表性（2026-07-24 集中）
INST_BACK_DAYS = 32    # 法人：INST_DAYS=20 交易日 ≈ 28 日曆日＋緩衝
BAL_BACK_DAYS = 12     # 融資/借券：BAL_DAYS=7 交易日
HOLD_START_DAYS = 21   # 千張大戶（週資料）：涵蓋最近 2-3 週
REV_BACK_DAYS = 830    # 月營收（sample 用）：涵蓋 REV_MONTHS=26 個月（≈790 日）＋緩衝
VAL_3Y_DAYS = 365 * 3  # PER/PBR 百分位樣本窗
VAL_DIV_DAYS = 550     # 股利公告查詢窗（近 18 個月）


def get_raw(url: str):
    """跨 repo raw JSON（無金鑰）。失敗重試一次，仍失敗回 None（欄位標 null）。"""
    for attempt in (1, 2):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                print(f"  ! {url} 失敗（{e}），重試一次")
                time.sleep(3)
    print(f"  ! {url} 二次失敗，相關欄位標 null")
    return None


def r2(v):
    return None if v is None else round(v, 2)


# ---------- 快取（data/diag/cache.json；增量抓、控制 API 用量） ----------

def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            print("  ! cache.json 壞掉，重建")
    return {}


def _append_panel(cache_sec: dict, date: str, day_map: dict, fields: int):
    """把「單一交易日全市場」資料補進 panel 快取。
    panel 結構 {"dates":[...], "stocks":{code:[[f1...],[f2...]...] 或 [v...]}}；
    fields=1 時每檔是一維陣列，>1 時每檔是 fields 條等長陣列。缺值補 None 對齊。"""
    dates = cache_sec.setdefault("dates", [])
    stocks = cache_sec.setdefault("stocks", {})
    n_old = len(dates)
    dates.append(date)
    for code, vals in day_map.items():
        arr = stocks.get(code)
        if arr is None:
            arr = [[None] * n_old for _ in range(fields)] if fields > 1 else [None] * n_old
            stocks[code] = arr
        if fields > 1:
            for i in range(fields):
                arr[i].append(vals[i])
        else:
            arr.append(vals)
    # 今日沒出現的舊代號也要補 None 對齊
    for code, arr in stocks.items():
        if fields > 1:
            for i in range(fields):
                if len(arr[i]) < len(dates):
                    arr[i].append(None)
        else:
            if len(arr) < len(dates):
                arr.append(None)


def _trim_panel(cache_sec: dict, keep: int, fields: int):
    dates = cache_sec.get("dates", [])
    if len(dates) > keep:
        cut = len(dates) - keep
        cache_sec["dates"] = dates[cut:]
        for code, arr in cache_sec.get("stocks", {}).items():
            if fields > 1:
                cache_sec["stocks"][code] = [a[cut:] for a in arr]
            else:
                cache_sec["stocks"][code] = arr[cut:]
    # 全 None 的代號（久未交易/下市）踢出快取
    dead = []
    for code, arr in cache_sec.get("stocks", {}).items():
        probe = arr[0] if fields > 1 else arr
        if all(v is None for v in probe):
            dead.append(code)
    for code in dead:
        del cache_sec["stocks"][code]


def _missing_dates(cached_dates: list, today: dt.date, backfill_days: int) -> list:
    """要補抓的日曆日（跳過週六日；假日抓到空清單自然略過）。"""
    if cached_dates:
        start = dt.date.fromisoformat(cached_dates[-1]) + dt.timedelta(days=1)
    else:
        start = today - dt.timedelta(days=backfill_days)
    out = []
    d = start
    while d <= today:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def update_price_cache(cache: dict, today: dt.date) -> dict | None:
    """價量 panel：TaiwanStockPrice 全市場逐日增量。每檔 3 條陣列：
    [收盤, 成交量(張), 成交值(千元)]。失敗且快取也空 -> None（宇宙無從建立）。"""
    sec = cache.setdefault("price", {})
    try:
        for d in _missing_dates(sec.get("dates", []), today, BACKFILL_DAYS):
            rows = api_get("TaiwanStockPrice", start_date=d, end_date=d)
            if not rows:
                continue
            day = {}
            for r in rows:
                code = r.get("stock_id", "")
                if not code or code == "TAIEX":
                    continue
                day[code] = [
                    r.get("close"),
                    round((r.get("Trading_Volume") or 0) / 1000),
                    round((r.get("Trading_money") or 0) / 1000),
                ]
            _append_panel(sec, d, day, fields=3)
            print(f"  price {d}: {len(day)} 檔")
        _trim_panel(sec, PRICE_DAYS, fields=3)
    except Exception as e:  # noqa: BLE001
        print(f"  ! 價量增量失敗（{e}），沿用既有快取")
    return sec if sec.get("dates") else None


def update_inst_cache(cache: dict, today: dt.date):
    """法人 panel：TaiwanStockInstitutionalInvestorsBuySell 全市場逐日增量。
    每檔 2 條陣列：[外資買賣超(張), 投信買賣超(張)]（外資含外資自營）。"""
    sec = cache.setdefault("inst", {})
    try:
        for d in _missing_dates(sec.get("dates", []), today, INST_BACK_DAYS):
            rows = api_get("TaiwanStockInstitutionalInvestorsBuySell", start_date=d, end_date=d)
            if not rows:
                continue
            day = {}
            for r in rows:
                code = r.get("stock_id", "")
                if not code:
                    continue
                net = ((r.get("buy") or 0) - (r.get("sell") or 0)) / 1000
                f, t = day.setdefault(code, [0.0, 0.0])
                name = r.get("name")
                if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
                    day[code][0] = f + net
                elif name == "Investment_Trust":
                    day[code][1] = t + net
            day = {c: [round(v[0]), round(v[1])] for c, v in day.items()}
            _append_panel(sec, d, day, fields=2)
            print(f"  inst {d}: {len(day)} 檔")
        _trim_panel(sec, INST_DAYS, fields=2)
    except Exception as e:  # noqa: BLE001
        print(f"  ! 法人增量失敗（{e}），沿用既有快取")
    return sec


def update_balance_cache(cache: dict, key: str, dataset: str, pick, today: dt.date):
    """餘額 panel（融資 mg／借券 sb）：全市場逐日增量，每檔一維陣列＝餘額(張)。"""
    sec = cache.setdefault(key, {})
    try:
        for d in _missing_dates(sec.get("dates", []), today, BAL_BACK_DAYS):
            rows = api_get(dataset, start_date=d, end_date=d)
            if not rows:
                continue
            day = {}
            for r in rows:
                code = r.get("stock_id", "")
                if code:
                    day[code] = pick(r)
            _append_panel(sec, d, day, fields=1)
            print(f"  {key} {d}: {len(day)} 檔")
        _trim_panel(sec, BAL_DAYS, fields=1)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {key} 增量失敗（{e}），沿用既有快取")
    return sec


def update_holding_cache(cache: dict, universe: list, today: dt.date):
    """千張大戶持股比（TaiwanStockHoldingSharesPer，週資料）：只能單檔查，
    每晚最多刷新 HOLD_CAP 檔、超過 HOLD_STALE_DAYS 天沒查過的才查，一週內輪完全宇宙。
    快取 {code:{"d":最新週資料日,"p":[最新%,前一週%],"chk":最後查詢日}}。"""
    sec = cache.setdefault("hold", {})
    today_s = today.isoformat()
    stale_before = (today - dt.timedelta(days=HOLD_STALE_DAYS)).isoformat()
    todo = [c for c in universe if (sec.get(c) or {}).get("chk", "") <= stale_before][:HOLD_CAP]
    print(f"  hold：待刷新 {len(todo)} 檔（上限 {HOLD_CAP}）")
    start = (today - dt.timedelta(days=HOLD_START_DAYS)).isoformat()
    done = 0
    for code in todo:
        try:
            rows = api_get("TaiwanStockHoldingSharesPer", data_id=code, start_date=start)
        except Exception as e:  # noqa: BLE001
            print(f"  ! hold 中斷於 {code}（{e}），已刷新 {done} 檔，其餘沿用快取")
            break
        by_date = {}
        for r in rows:
            if r.get("HoldingSharesLevel") == "more than 1,000,001":
                by_date[r.get("date")] = r.get("percent")
        ent = sec.setdefault(code, {})
        ent["chk"] = today_s
        if by_date:
            ds = sorted(by_date)[-2:]
            ent["d"] = ds[-1]
            ent["p"] = [by_date[d] for d in reversed(ds)]  # [最新, 前一週]
        done += 1
    return sec


# ---------- 基本面（子期2）：月營收 / 估值百分位 / 除權息 / 處置注意 ----------

def _rev_key(r) -> str:
    return f"{int(r.get('revenue_year')):04d}-{int(r.get('revenue_month')):02d}"


def _month_iter(end: dt.date, n: int):
    """回傳含 end 當月往前共 n 個月的 (year, month) 清單（升冪）。"""
    y, m = end.year, end.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def update_rev_cache(cache: dict, today: dt.date):
    """月營收 panel（FinMind TaiwanStockMonthRevenue，全市場逐「公布月」查詢）。
    快取 {"months":[營收月 "YYYY-MM" 升冪], "stocks":{code:[營收(元)...]}, "pub":[已抓公布月]}。
    每晚重抓當月＋前月兩個公布月（涵蓋遲交補報）；首跑回補 REV_MONTHS+1 個公布月。"""
    sec = cache.setdefault("rev", {})
    months = sec.setdefault("months", [])
    stocks = sec.setdefault("stocks", {})
    pub_done = set(sec.get("pub") or [])
    all_pub = [f"{y:04d}-{m:02d}" for y, m in _month_iter(today, REV_MONTHS + 1)]
    todo = [p for p in all_pub if p not in pub_done][-(REV_MONTHS + 1):]
    # 已回補過 -> 只重抓最近兩個公布月（遲交/更正）
    if len(todo) <= 1:
        todo = all_pub[-2:]
    try:
        for p in todo:
            y, m = int(p[:4]), int(p[5:7])
            last_day = (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)).isoformat()
            rows = api_get("TaiwanStockMonthRevenue", start_date=f"{p}-01", end_date=last_day)
            for r in rows:
                code, k, rev = r.get("stock_id", ""), _rev_key(r), r.get("revenue")
                if not code or rev is None:
                    continue
                if k not in months:
                    months.append(k)
                    months.sort()
                    for arr in stocks.values():
                        arr.insert(months.index(k), None)
                arr = stocks.setdefault(code, [None] * len(months))
                while len(arr) < len(months):
                    arr.append(None)
                arr[months.index(k)] = rev
            pub_done.add(p)
            print(f"  rev {p}: {len(rows)} 列")
        # 修剪至 REV_MONTHS 個營收月
        if len(months) > REV_MONTHS:
            cut = len(months) - REV_MONTHS
            sec["months"] = months[cut:]
            for code in list(stocks):
                stocks[code] = stocks[code][cut:]
        sec["pub"] = sorted(pub_done)[-(REV_MONTHS + 2):]
    except Exception as e:  # noqa: BLE001
        print(f"  ! rev 增量失敗（{e}），沿用既有快取")
    return sec


def fetch_per_daily(today: dt.date):
    """本益比/淨值比/殖利率當日值（FinMind TaiwanStockPER，全市場單日、回退 5 天）。"""
    try:
        for back in range(6):
            d = (today - dt.timedelta(days=back)).isoformat()
            rows = api_get("TaiwanStockPER", start_date=d, end_date=d)
            if rows:
                print(f"  per {d}: {len(rows)} 檔")
                return d, {r["stock_id"]: r for r in rows if r.get("stock_id")}
    except Exception as e:  # noqa: BLE001
        print(f"  ! per 當日值失敗（{e}），pe/pb/dy 標 null")
    return None, {}


def _pctile(series: list, cur: float):
    """cur 在 series（去 0/None）中的百分位（<=cur 的比例 ×100）。樣本 <60 不算。"""
    vals = [v for v in series if v]
    if len(vals) < 60 or not cur:
        return None
    return round(sum(1 for v in vals if v <= cur) / len(vals) * 100, 1)


def _next_exdiv(rows: list, today_s: str):
    """從 TaiwanStockDividend 公告列找下一個（>= 今日）除權/除息交易日。
    先蒐集全部候選日再取最小、彙整該日所有 kind——舊版邊掃邊更新 best，
    同日現金＋股票的合併依賴輸入順序、特定順序下會漏合（2026-07-24 改寫）。"""
    cand: dict[str, set] = {}
    for r in rows:
        for f, kind in (("CashExDividendTradingDate", "現金"), ("StockExDividendTradingDate", "股票")):
            d = (r.get(f) or "").strip()
            if d and d >= today_s:
                cand.setdefault(d, set()).add(kind)
    if not cand:
        return None, None
    d = min(cand)
    return d, ("現金+股票" if len(cand[d]) == 2 else next(iter(cand[d])))


def update_val_cache(cache: dict, universe: list, today: dt.date):
    """估值/除權息輪替刷新（只能單檔查）：每晚最多 VAL_CAP 檔、VAL_STALE_DAYS 天輪一次。
    - PER/PBR 3年百分位：FinMind TaiwanStockPER 單檔 3 年序列，取當前值的歷史百分位
    - 除權息日程：FinMind TaiwanStockDividend 單檔近 18 個月公告，取下一個除權息交易日
    快取 {code:{"pep","pbp","exd","exk","chk"}}；chk=最後刷新日（前端顯示時效）。"""
    sec = cache.setdefault("val", {})
    today_s = today.isoformat()
    stale_before = (today - dt.timedelta(days=VAL_STALE_DAYS)).isoformat()
    todo = [c for c in universe if (sec.get(c) or {}).get("chk", "") <= stale_before][:VAL_CAP]
    print(f"  val：待刷新 {len(todo)} 檔（上限 {VAL_CAP}）")
    start3y = (today - dt.timedelta(days=VAL_3Y_DAYS)).isoformat()
    start18m = (today - dt.timedelta(days=VAL_DIV_DAYS)).isoformat()
    done = 0
    for code in todo:
        ent = sec.setdefault(code, {})
        try:
            rows = api_get("TaiwanStockPER", data_id=code, start_date=start3y)
            cur = rows[-1] if rows else {}
            ent["pep"] = _pctile([r.get("PER") for r in rows], cur.get("PER"))
            ent["pbp"] = _pctile([r.get("PBR") for r in rows], cur.get("PBR"))
            rows2 = api_get("TaiwanStockDividend", data_id=code, start_date=start18m)
            ent["exd"], ent["exk"] = _next_exdiv(rows2, today_s)
            ent["chk"] = today_s
        except Exception as e:  # noqa: BLE001
            print(f"  ! val 中斷於 {code}（{e}），已刷新 {done} 檔，其餘沿用快取")
            break
        done += 1
    return sec


def _roc_date(s: str):
    """民國日期 '115/07/15' 或 '1150715' -> ISO；解析失敗回 None。"""
    s = (s or "").strip().replace("／", "/")
    try:
        if "/" in s:
            y, m, d = s.split("/")
        elif len(s) == 7:
            y, m, d = s[:3], s[3:5], s[5:7]
        else:
            return None
        return dt.date(int(y) + 1911, int(m), int(d)).isoformat()
    except Exception:
        return None


def fetch_disposal(today: dt.date) -> tuple[dict, set, bool]:
    """處置/注意股（交易所公開 OpenAPI，無金鑰）。
    - TWSE 處置：openapi.twse.com.tw /v1/announcement/punish（DispositionPeriod 民國區間）
    - TPEx 處置：tpex.org.tw /openapi/v1/tpex_disposal_information（verify 關閉：該站憑證
      缺 Subject Key Identifier，OpenSSL 3.x 驗不過；唯讀公開公告資料，風險可受）
    - TWSE 注意：/v1/announcement/notice（當日注意股清單）
    - TPEx 注意：官方 OpenAPI swagger 無此端點（2026-07 查證），列待辦
    回傳 (處置dict或None, 注意set或None)；任一處置端點失敗 -> 處置整欄 None
    （無法區分「不在清單」與「端點壞」，寧標 null 不誤標 0）。"""
    today_s = today.isoformat()
    disp, attn = {}, set()
    disp_ok, attn_ok = True, True

    def hit(url, verify=True):
        # UA 帶瀏覽器字串：TWSE/TPEx OpenAPI 對 python-requests 預設 UA／資料中心流量
        # 較易回 403 或 HTML 擋頁（2026-07-18 Actions 首跑 dp/at 全 null 的疑因）
        hdr = {"accept": "application/json",
               "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        for attempt in (1, 2):
            try:
                r = requests.get(url, timeout=30, headers=hdr, verify=verify)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"  ! {url} 失敗（{e}）")
                    return None
                time.sleep(3)

    j = hit("https://openapi.twse.com.tw/v1/announcement/punish")
    if isinstance(j, list):
        for r in j:
            code = (r.get("Code") or "").strip()
            period = (r.get("DispositionPeriod") or "").replace("～", "~")
            parts = [p for p in period.split("~") if p.strip()]
            d0 = _roc_date(parts[0]) if parts else None
            d1 = _roc_date(parts[-1]) if parts else None
            if code and d0 and d1 and d0 <= today_s <= d1:
                disp[code] = d1
    else:
        disp_ok = False
    import urllib3
    urllib3.disable_warnings()
    j = hit("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", verify=False)
    if isinstance(j, list):
        for r in j:
            code = (r.get("SecuritiesCompanyCode") or "").strip()
            period = (r.get("DispositionPeriod") or "").replace("～", "~")
            parts = [p for p in period.split("~") if p.strip()]
            d0 = _roc_date(parts[0]) if parts else None
            d1 = _roc_date(parts[-1]) if parts else None
            if code and d0 and d1 and d0 <= today_s <= d1:
                disp[code] = d1
    else:
        disp_ok = False
    j = hit("https://openapi.twse.com.tw/v1/announcement/notice")
    if isinstance(j, list):
        for r in j:
            code = (r.get("Code") or "").strip()
            if code:
                attn.add(code)
    else:
        attn_ok = False
    print(f"  disposal：處置中 {len(disp)} 檔、TWSE 當日注意 {len(attn)} 檔"
          f"（TPEx 注意股無端點，待辦）{'' if disp_ok and attn_ok else ' !! 部分端點失敗'}")
    return (disp if disp_ok else None), (attn if attn_ok else None)


def rev_metrics(months: list, arr: list):
    """月營收指標：ym=最新有值營收月、yoy/mom(%)、rvs=連續YoY成長(+)/衰退(-)月數。"""
    if not months or not arr:
        return None
    idx = {m: i for i, m in enumerate(months)}
    li = max((i for i, v in enumerate(arr) if v), default=None)
    if li is None:
        return None
    def yoy_at(i):
        m = months[i]
        pi = idx.get(f"{int(m[:4])-1:04d}-{m[5:7]}")
        if pi is None or not arr[pi] or not arr[i]:
            return None
        return (arr[i] / arr[pi] - 1) * 100
    yoy = yoy_at(li)
    mom = (arr[li] / arr[li-1] - 1) * 100 if li >= 1 and arr[li-1] and arr[li] else None
    rvs = 0
    if yoy is not None:
        sign = 1 if yoy > 0 else -1 if yoy < 0 else 0
        i = li
        while sign and i >= 0:
            y = yoy_at(i)
            if y is None or (y > 0) != (sign > 0) or y == 0:
                break
            rvs += 1
            i -= 1
        rvs *= sign
    return {"ym": months[li], "yoy": r2(yoy), "mom": r2(mom), "rvs": rvs}


# ---------- 指標計算 ----------

def _win(arr, n, end_off=0):
    """取尾端視窗（排除最後 end_off 筆），任一 None 或長度不足 -> None。"""
    stop = len(arr) - end_off
    if stop - n < 0:
        return None
    w = arr[stop - n:stop]
    return None if any(v is None for v in w) else w


def _win_loose(arr, n, end_off=0, min_n=1):
    """寬鬆視窗：去 None 後至少 min_n 筆才回傳（新上市/停牌容忍）。"""
    stop = len(arr) - end_off
    w = [v for v in arr[max(0, stop - n):stop] if v is not None]
    return w if len(w) >= min_n else None


def streak(vals):
    """連續同號天數：尾端起算，正=連買、負=連賣；最新一天為 0/None 則 0。"""
    if not vals or vals[-1] is None or vals[-1] == 0:
        return 0
    sign = 1 if vals[-1] > 0 else -1
    n = 0
    for v in reversed(vals):
        if v is None or v == 0 or (v > 0) != (sign > 0):
            break
        n += 1
    return sign * n


def build_stock_fields(code, price, inst, mg, sb, hold, cls_map, morning, pm_map, pm_date, price_date, mkt_r20,
                       rev_sec=None, per_map=None, val_map=None, disp=None, attn=None, today=None):
    """單檔欄位（壓縮欄名）。每欄資料源見行內註解。"""
    c_arr, v_arr, a_arr = price
    c = c_arr[-1]
    pc = c_arr[-2] if len(c_arr) >= 2 else None
    o = {}

    # --- 價量（FinMind TaiwanStockPrice） ---
    o["c"] = c                                        # 收盤價（診斷基準日）
    o["pc"] = pc                                      # 前一日收盤
    o["d1"] = r2((c / pc - 1) * 100) if c and pc else None   # 今日漲跌%
    for k, n in (("ma5", 5), ("ma20", 20), ("ma60", 60)):
        w = _win(c_arr, n)
        o[k] = r2((c / (sum(w) / n) - 1) * 100) if c and w else None  # 現價距 MA%
    w = _win_loose(c_arr, 20, end_off=1, min_n=15)
    o["lo20"] = (1 if c < min(w) else 0) if c and w else None  # 破20日新低 flag
    w = _win_loose(c_arr, 60, min_n=30)
    o["dd60"] = r2((c / max(w) - 1) * 100) if c and w else None  # 距60日高%（回撤）
    o["r5"] = r2((c / c_arr[-6] - 1) * 100) if len(c_arr) >= 6 and c and c_arr[-6] else None   # 5日報酬%
    o["r20"] = r2((c / c_arr[-21] - 1) * 100) if len(c_arr) >= 21 and c and c_arr[-21] else None  # 20日報酬%
    o["rs"] = r2(o["r20"] - mkt_r20) if o["r20"] is not None and mkt_r20 is not None else None  # RS：20日報酬－大盤同期
    vw = _win_loose(v_arr, 20, end_off=1, min_n=15)
    avg_v = sum(vw) / len(vw) if vw else None
    v_today = v_arr[-1]
    o["vs"] = (1 if v_today > 2 * avg_v else 0) if v_today is not None and avg_v else None  # 爆量 flag（量>20日均量2倍）
    o["vb"] = 1 if o["vs"] and o["d1"] is not None and o["d1"] <= -3 else (0 if o["vs"] is not None else None)  # 爆量長黑 flag
    aw = _win_loose(a_arr, 20, min_n=5)
    o["av20"] = round(sum(aw) / len(aw)) if aw else None  # 20日日均成交值（千元；流動性檢查用）

    # --- 籌碼：外資/投信（FinMind TaiwanStockInstitutionalInvestorsBuySell） ---
    if inst:
        f_arr, t_arr = inst
        o["fd"] = streak(f_arr)                        # 外資連買(+)/連賣(-)天數
        o["td"] = streak(t_arr)                        # 投信連買/連賣天數
        f5 = _win_loose(f_arr, 5, min_n=3)
        t5 = _win_loose(t_arr, 5, min_n=3)
        o["f5"] = round(sum(f5)) if f5 else None       # 外資近5日合計(張)
        o["t5"] = round(sum(t5)) if t5 else None       # 投信近5日合計(張)
        fl, tl = f_arr[-1], t_arr[-1]
        o["bb"] = 1 if (fl or 0) > 0 and (tl or 0) > 0 else (None if fl is None else 0)  # 土洋同買 flag（今日外資、投信同步買超）
    else:
        o.update(fd=None, td=None, f5=None, t5=None, bb=None)

    # --- 籌碼：融資 5 日增減%（FinMind TaiwanStockMarginPurchaseShortSale，餘額張） ---
    o["mg5"] = None
    if mg and len(mg) >= 6 and mg[-1] and mg[-6]:
        o["mg5"] = r2((mg[-1] / mg[-6] - 1) * 100)

    # --- 籌碼：借券賣出餘額 5 日變化(張)（FinMind TaiwanDailyShortSaleBalances） ---
    o["lb5"] = None
    if sb and len(sb) >= 6 and sb[-1] is not None and sb[-6] is not None:
        o["lb5"] = round(sb[-1] - sb[-6])

    # --- 籌碼：券資比／當沖比（data/postmkt.json 既有管線 merge；日期不合則 null） ---
    pm = pm_map.get(code)
    same_day = pm is not None and pm_date and pm_date == price_date
    o["cr"] = r2(pm.get("credit_ratio")) if same_day and pm.get("credit_ratio") is not None else None  # 券資比%
    dtv = pm.get("dt_vol") if same_day else None       # 當沖量(股)
    o["dt"] = r2(dtv / 1000 / v_today * 100) if dtv and v_today else None  # 當沖比%＝當沖量/當日總量（張）

    # --- 籌碼：千張大戶持股比週變化（FinMind TaiwanStockHoldingSharesPer，週資料快取） ---
    h = hold or {}
    o["hd"] = h.get("p")                               # [最新週%, 前一週%]
    o["hdd"] = h.get("d")                              # 最新週資料日
    o["hdw"] = r2(h["p"][0] - h["p"][1]) if h.get("p") and len(h["p"]) == 2 and None not in h["p"] else None  # 週變化(百分點)

    # --- 題材（taiwan-flow-live-v2 classify.json ＋ morning.json 訊號清單） ---
    cm = (cls_map or {}).get(code) or {}
    o["n"] = cm.get("n") or (pm.get("n") if pm else "") or ""
    o["ind"] = cm.get("e")                             # 交易所產業別
    subs, chains = [], []
    for pair in cm.get("p") or []:
        if len(pair) == 2:
            if pair[0] not in chains:
                chains.append(pair[0])
            if pair[1] not in subs:
                subs.append(pair[1])
    o["sub"] = subs or None                            # 次產業
    o["ch"] = chains or None                           # 產業鏈
    cont = set((morning or {}).get("signals", {}).get("cont_subs") or [])
    down = set((morning or {}).get("recap", {}).get("down_subs") or [])
    o["cs"] = (1 if any(s in cont for s in subs) else 0) if subs and morning else None  # 次產業在連湧清單（已回測訊號）
    o["ds"] = (1 if any(s in down for s in subs) else 0) if subs and morning else None  # 次產業在昨日退湧清單

    # --- 基本面：月營收（FinMind TaiwanStockMonthRevenue） ---
    rm = rev_metrics((rev_sec or {}).get("months") or [], ((rev_sec or {}).get("stocks") or {}).get(code) or [])
    o["rym"] = rm["ym"] if rm else None                # 最新營收月
    o["yoy"] = rm["yoy"] if rm else None               # 營收YoY%
    o["mom"] = rm["mom"] if rm else None               # 營收MoM%
    o["rvs"] = rm["rvs"] if rm else None               # 連續YoY成長(+)/衰退(-)月數

    # --- 基本面：估值當日值（FinMind TaiwanStockPER 全市場單日） ---
    pr = (per_map or {}).get(code) or {}
    o["pe"] = r2(pr.get("PER")) or None                # 本益比（0=虧損/無值 -> null）
    o["pb"] = r2(pr.get("PBR")) or None                # 股價淨值比
    o["dy"] = r2(pr.get("dividend_yield")) or None     # 殖利率%

    # --- 基本面：3年百分位＋除權息（FinMind 單檔輪替快取 val） ---
    v = (val_map or {}).get(code) or {}
    o["pep"] = v.get("pep")                            # PER 3年百分位（刷新日 vch）
    o["pbp"] = v.get("pbp")                            # PBR 3年百分位
    o["vch"] = v.get("chk")                            # 百分位/除權息最後刷新日
    exd = v.get("exd")
    in60 = bool(exd and today and exd <= (today + dt.timedelta(days=60)).isoformat())
    o["exd"] = exd if in60 else None                   # 近60日內除權息交易日
    o["exk"] = v.get("exk") if in60 else None          # 現金/股票/現金+股票

    # --- 基本面：處置/注意（TWSE/TPEx OpenAPI；TPEx 注意股無端點列待辦） ---
    o["dp"] = 1 if disp is not None and code in disp else (0 if disp is not None else None)   # 處置中 flag
    o["dpu"] = disp.get(code) if disp else None        # 處置迄日
    o["at"] = 1 if attn is not None and code in attn else (0 if attn is not None else None)   # TWSE 當日注意 flag
    return o


def build_market(taiex, price_sec, morning, us):
    """market 區塊（單一物件）。資料源：TAIEX=FinMind TaiwanStockPrice(data_id=TAIEX)；
    夜盤 gap=morning.json；美股=us.json；寬度=價量 panel 全市場漲跌家數。"""
    m = {}
    if taiex:
        cs = [r.get("close") for r in taiex if r.get("close")]
        c = cs[-1] if cs else None
        m["date"] = taiex[-1].get("date") if taiex else None
        m["close"] = c
        m["ma20"] = r2((c / (sum(cs[-20:]) / 20) - 1) * 100) if c and len(cs) >= 20 else None  # 大盤距MA20%
        m["ma60"] = r2((c / (sum(cs[-60:]) / 60) - 1) * 100) if c and len(cs) >= 60 else None  # 大盤距MA60%
        m["dd60"] = r2((c / max(cs[-60:]) - 1) * 100) if c and cs else None                    # 距60日高%
        m["ret20"] = r2((c / cs[-21] - 1) * 100) if c and len(cs) >= 21 and cs[-21] else None  # 大盤20日報酬（RS基準）
        # regime：距 60 日高回撤 >10% ＝修正 regime（回測發現「退出被買回」在修正月失效）
        m["regime"] = ("correction" if m["dd60"] is not None and m["dd60"] <= -10 else "normal") if m["dd60"] is not None else None
    else:
        m.update(date=None, close=None, ma20=None, ma60=None, dd60=None, ret20=None, regime=None)

    # 近5日市場寬度：全市場「上漲家數/有漲跌家數」的 5 日均值（價量 panel 自算）
    m["breadth5"] = None
    if price_sec and len(price_sec.get("dates", [])) >= 6:
        stocks = price_sec["stocks"]
        ratios = []
        n_dates = len(price_sec["dates"])
        for i in range(n_dates - 5, n_dates):
            up = dn = 0
            for arr in stocks.values():
                a, b = arr[0][i - 1], arr[0][i]
                if a and b:
                    if b > a:
                        up += 1
                    elif b < a:
                        dn += 1
            if up + dn:
                ratios.append(up / (up + dn))
        m["breadth5"] = r2(sum(ratios) / len(ratios) * 100) if ratios else None

    # 台指期夜盤 gap（taiwan-flow-live-v2 morning.json：夜盤收盤 vs 現貨）
    g = (morning or {}).get("gap") or {}
    m["fut"] = {"date": g.get("date"), "close": g.get("close"), "chg_pct": g.get("chg_pct"),
                "gap": g.get("gap"), "spot": g.get("spot")} if g else None
    # 美股/費半/ADR 摘要（taiwan-flow-live-v2 us.json）
    m["us"] = {"date": (us or {}).get("date"), "brief": (us or {}).get("brief"),
               "session": (us or {}).get("session")} if us else None
    # 連湧/退湧次產業清單（v2 morning.json，已回測訊號；前端題材燈號比對用）
    m["cont_subs"] = (morning or {}).get("signals", {}).get("cont_subs") if morning else None
    m["down_subs"] = (morning or {}).get("recap", {}).get("down_subs") if morning else None
    m["morning_date"] = (morning or {}).get("date") if morning else None
    return m


# ---------- sample 模式（本地流程驗證：少量代號、單檔查詢、可無 token） ----------

def fetch_sample(codes, today):
    """單檔 range 查詢組 panel（同 full 模式資料結構），來源失敗印警告標 null。"""
    start_p = (today - dt.timedelta(days=BACKFILL_DAYS)).isoformat()
    price, inst, mg, sb, hold = {}, {}, {}, {}, {}
    price_dates = []
    for code in codes:
        try:
            rows = api_get("TaiwanStockPrice", data_id=code, start_date=start_p)
            rows = rows[-PRICE_DAYS:]
            price[code] = {r["date"]: [r.get("close"), round((r.get("Trading_Volume") or 0) / 1000),
                                       round((r.get("Trading_money") or 0) / 1000)] for r in rows}
            price_dates = sorted(set(price_dates) | set(price[code]))
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample price {code}: {e}")
    price_dates = price_dates[-PRICE_DAYS:]
    price_sec = {"dates": price_dates, "stocks": {}}
    for code, by_d in price.items():
        arrs = [[], [], []]
        for d in price_dates:
            v = by_d.get(d, [None, None, None])
            for i in range(3):
                arrs[i].append(v[i])
        price_sec["stocks"][code] = arrs

    start_i = (today - dt.timedelta(days=INST_BACK_DAYS)).isoformat()
    for code in codes:
        try:
            rows = api_get("TaiwanStockInstitutionalInvestorsBuySell", data_id=code, start_date=start_i)
            by_d = {}
            for r in rows:
                net = ((r.get("buy") or 0) - (r.get("sell") or 0)) / 1000
                f, t = by_d.setdefault(r["date"], [0.0, 0.0])
                if r.get("name") in ("Foreign_Investor", "Foreign_Dealer_Self"):
                    by_d[r["date"]][0] = f + net
                elif r.get("name") == "Investment_Trust":
                    by_d[r["date"]][1] = t + net
            ds = sorted(by_d)[-INST_DAYS:]
            inst[code] = [[round(by_d[d][0]) for d in ds], [round(by_d[d][1]) for d in ds]]
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample inst {code}: {e}")

    start_b = (today - dt.timedelta(days=BAL_BACK_DAYS)).isoformat()
    for code in codes:
        try:
            rows = api_get("TaiwanStockMarginPurchaseShortSale", data_id=code, start_date=start_b)
            mg[code] = [r.get("MarginPurchaseTodayBalance") or 0 for r in rows][-BAL_DAYS:]
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample margin {code}: {e}")
        try:
            rows = api_get("TaiwanDailyShortSaleBalances", data_id=code, start_date=start_b)
            sb[code] = [round((r.get("SBLShortSalesCurrentDayBalance") or 0) / 1000) for r in rows][-BAL_DAYS:]
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample shortbal {code}: {e}")
        try:
            rows = api_get("TaiwanStockHoldingSharesPer", data_id=code,
                           start_date=(today - dt.timedelta(days=HOLD_START_DAYS)).isoformat())
            by_date = {r["date"]: r.get("percent") for r in rows
                       if r.get("HoldingSharesLevel") == "more than 1,000,001"}
            if by_date:
                ds = sorted(by_date)[-2:]
                hold[code] = {"d": ds[-1], "p": [by_date[d] for d in reversed(ds)]}
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample hold {code}: {e}")

    # 基本面（子期2）：月營收 / 估值 / 除權息，單檔查詢版
    rev_sec = {"months": [], "stocks": {}}
    per_map, val_map = {}, {}
    start_rev = (today - dt.timedelta(days=REV_BACK_DAYS)).isoformat()
    start3y = (today - dt.timedelta(days=VAL_3Y_DAYS)).isoformat()
    start18m = (today - dt.timedelta(days=VAL_DIV_DAYS)).isoformat()
    raw_rev = {}
    for code in codes:
        try:
            raw_rev[code] = api_get("TaiwanStockMonthRevenue", data_id=code, start_date=start_rev)
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample rev {code}: {e}")
    all_months = sorted({_rev_key(r) for rows in raw_rev.values() for r in rows})
    rev_sec["months"] = all_months
    for code, rows in raw_rev.items():
        arr = [None] * len(all_months)
        for r in rows:
            arr[all_months.index(_rev_key(r))] = r.get("revenue")
        rev_sec["stocks"][code] = arr
    for code in codes:
        try:
            rows = api_get("TaiwanStockPER", data_id=code, start_date=start3y)
            if rows:
                per_map[code] = rows[-1]
                val_map[code] = {"pep": _pctile([r.get("PER") for r in rows], rows[-1].get("PER")),
                                 "pbp": _pctile([r.get("PBR") for r in rows], rows[-1].get("PBR")),
                                 "chk": today.isoformat()}
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample per {code}: {e}")
        try:
            rows = api_get("TaiwanStockDividend", data_id=code, start_date=start18m)
            ent = val_map.setdefault(code, {"chk": today.isoformat()})
            ent["exd"], ent["exk"] = _next_exdiv(rows, today.isoformat())
        except Exception as e:  # noqa: BLE001
            print(f"  ! sample dividend {code}: {e}")
    return price_sec, inst, mg, sb, hold, rev_sec, per_map, val_map


# ---------- 主流程 ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", nargs="?", const=",".join(SAMPLE_CODES), default=None,
                    help="本地驗證模式：逗號分隔代號（預設 %s）" % ",".join(SAMPLE_CODES))
    ap.add_argument("--out", default=None, help="輸出路徑（預設 data/diag/diag.json）")
    args = ap.parse_args()
    sample = bool(args.sample)
    out_path = Path(args.out) if args.out else OUT_PATH
    today = taipei_today()

    if not sample and not token():
        raise RuntimeError("找不到環境變數 FINMIND_TOKEN（本地驗證請用 --sample）")

    print("== 跨 repo 素材（raw，無金鑰） ==")
    cls = get_raw(V2_RAW + "/classify.json")
    cls_map = (cls or {}).get("map") or {}
    morning = get_raw(V2_RAW + "/morning.json")
    us = get_raw(V2_RAW + "/us.json")

    print("== TAIEX（FinMind TaiwanStockPrice data_id=TAIEX） ==")
    taiex = None
    try:
        taiex = api_get("TaiwanStockPrice", data_id="TAIEX",
                        start_date=(today - dt.timedelta(days=BACKFILL_DAYS)).isoformat())
    except Exception as e:  # noqa: BLE001
        print(f"  ! TAIEX 失敗（{e}），market 欄位標 null")

    pm_map, pm_date = {}, None
    if PM_PATH.exists():
        try:
            pm = json.loads(PM_PATH.read_text(encoding="utf-8"))
            pm_date = ((pm.get("lending") or {}).get("date")) or pm.get("date")
            for r in (pm.get("lending") or {}).get("rows") or []:
                pm_map[r.get("c")] = r
        except Exception as e:  # noqa: BLE001
            print(f"  ! postmkt.json 讀取失敗（{e}），券資比/當沖比標 null")

    print("== 處置/注意股（交易所 OpenAPI，無金鑰） ==")
    disp, attn = fetch_disposal(today)

    if sample:
        codes = [c.strip() for c in args.sample.split(",") if c.strip()]
        print(f"== sample 模式：{codes} ==")
        price_sec, inst_map, mg_map, sb_map, hold_map, rev_sec, per_map, val_map = fetch_sample(codes, today)
        universe = [c for c in codes if c in price_sec["stocks"]]
        cache = None
    else:
        cache = load_cache()
        print("== 價量 panel（增量） ==")
        price_sec = update_price_cache(cache, today)
        if not price_sec:
            raise RuntimeError("價量資料抓取失敗且無快取，無法建立宇宙")
        print("== 法人 panel（增量） ==")
        inst_sec = update_inst_cache(cache, today)
        print("== 融資/借券餘額 panel（增量） ==")
        mg_sec = update_balance_cache(cache, "mg", "TaiwanStockMarginPurchaseShortSale",
                                      lambda r: r.get("MarginPurchaseTodayBalance") or 0, today)
        sb_sec = update_balance_cache(cache, "sb", "TaiwanDailyShortSaleBalances",
                                      lambda r: round((r.get("SBLShortSalesCurrentDayBalance") or 0) / 1000), today)

        # 宇宙：20日日均成交值前 UNIVERSE_N 檔（上市＋上櫃；排除權證等非 classify/非4碼代號）
        scored = []
        for code, arr in price_sec["stocks"].items():
            if not (code in cls_map or (len(code) == 4 and code.isdigit())):
                continue
            aw = _win_loose(arr[2], 20, min_n=5)
            if aw and arr[0][-1] is not None:
                scored.append((sum(aw) / len(aw), code))
        scored.sort(reverse=True)
        universe = [c for _, c in scored[:UNIVERSE_N]]
        keep = {c for _, c in scored[:CACHE_N]}
        price_sec["stocks"] = {c: a for c, a in price_sec["stocks"].items() if c in keep}
        print(f"  宇宙 {len(universe)} 檔（快取保留 {len(price_sec['stocks'])} 檔）")

        print("== 千張大戶（單檔輪替刷新） ==")
        hold_sec = update_holding_cache(cache, universe, today)
        print("== 月營收 panel（增量） ==")
        rev_sec = update_rev_cache(cache, today)
        print("== 估值當日值（全市場單日） ==")
        _, per_map = fetch_per_daily(today)
        print("== 估值百分位/除權息（單檔輪替刷新） ==")
        val_sec = update_val_cache(cache, universe, today)
        inst_sec["stocks"] = {c: a for c, a in inst_sec.get("stocks", {}).items() if c in keep}
        mg_sec["stocks"] = {c: a for c, a in mg_sec.get("stocks", {}).items() if c in keep}
        sb_sec["stocks"] = {c: a for c, a in sb_sec.get("stocks", {}).items() if c in keep}
        rev_sec["stocks"] = {c: a for c, a in rev_sec.get("stocks", {}).items() if c in keep}
        cache["hold"] = {c: v for c, v in hold_sec.items() if c in keep}
        cache["val"] = {c: v for c, v in val_sec.items() if c in keep}
        inst_map = inst_sec.get("stocks", {})
        mg_map = mg_sec.get("stocks", {})
        sb_map = sb_sec.get("stocks", {})
        hold_map = cache["hold"]
        val_map = cache["val"]

    print("== 組欄位 ==")
    mkt = build_market(taiex, None if sample else cache.get("price"), morning, us)
    price_date = price_sec["dates"][-1] if price_sec.get("dates") else None
    stocks = {}
    for code in universe:
        arr = price_sec["stocks"].get(code)
        if not arr or arr[0][-1] is None:
            continue
        stocks[code] = build_stock_fields(
            code, arr, inst_map.get(code), mg_map.get(code), sb_map.get(code),
            hold_map.get(code), cls_map, morning, pm_map, pm_date, price_date, mkt.get("ret20"),
            rev_sec=rev_sec, per_map=per_map, val_map=val_map, disp=disp, attn=attn, today=today)

    out = {
        "date": price_date,
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "mode": "sample" if sample else "full",
        "n": len(stocks),
        "pm_date": pm_date,
        # 子期2 待辦註記：TPEx 官方 OpenAPI（swagger 2026-07 查證）無上櫃「注意股」端點，
        # at 欄位僅涵蓋 TWSE；補上櫃注意股列待辦
        "notes": {"attention": "at 僅含 TWSE 注意股（TPEx 無公開端點，待辦）"},
        "market": mkt,
        "stocks": stocks,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"輸出 {out_path}（{out_path.stat().st_size/1e6:.2f} MB，{len(stocks)} 檔，資料日 {price_date}）")

    if not sample and cache is not None:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"快取 {CACHE_PATH}（{CACHE_PATH.stat().st_size/1e6:.2f} MB）")


if __name__ == "__main__":
    main()
