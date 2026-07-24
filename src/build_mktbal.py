# src/build_mktbal.py — 「大盤餘額」tab 資料管線（全市場層級四項餘額）
#
# 產出 data/market_balance_history.json，供前端「大盤餘額」tab 讀取。定位＝大盤層級籌碼
# （全市場合計），與既有「融借券」tab（個股層級排行）明確區分。
#
# ════════════════════════════════════════════════════════════════
# 四項欄位語意與單位（JSON schema 已鎖定，前端依此讀取，勿更動欄位名稱）
# ════════════════════════════════════════════════════════════════
#   margin_shares        融資餘額（張）        FinMind MarginPurchase.TodayBalance
#   margin_money          融資餘額（元）        FinMind MarginPurchaseMoney.TodayBalance
#   short_shares          融券餘額（張）        FinMind ShortSale.TodayBalance
#   sbl_short_shares       借券賣出餘額（股）     TWSE TWT72U，見下方口徑
#   sbl_short_value        借券賣出餘額市值（元） TWSE TWT72U，見下方口徑
#   unrestricted_shares    不限用途款項借貸餘額（仟股） TWSE TWTA1U，見下方口徑
#
# 來源 1：FinMind TaiwanStockTotalMarginPurchaseShortSale（Free、全市場、逐日、3年可得）。
#   token 讀環境變數 FINMIND_TOKEN。單次查詢 3 年範圍已實測可行（0.7s、727 交易日、2181 列，
#   未觸發截斷），不需按年分段。
#
# 來源 2（口徑已實測驗證）：借券賣出餘額 = TWSE TWT72U 的 selectType=SLB 與 selectType=NLB
#   「整體市場」合計列 兩者相加。
#   實測 2026-07-17：TWT72U 回傳的 data 陣列末端固定有 3 列合計（依 fields[-1]「市場別」
#   分別是「集中市場」「櫃檯買賣中心」「整體市場」），「整體市場」列即為集中+櫃買加總，
#   欄位 row[5]=本日借券餘額股(4)、row[7]=借券餘額市值(6)=(4)*(5)，語意單一、可直接採用。
#   SLB（借券系統）與 NLB（證券商營業處所借券/全體證券商辦理有價證券借貸）是兩個獨立
#   借券管道，各自的「整體市場」都已是市場層級合計、彼此不重疊 → 直接相加才是完整借券
#   賣出餘額。已用 2026-07-17 實際數字核對：SLB 整體市場 16,910,017,000 股 /
#   2,718,883,468,980 元；NLB 整體市場 13,331,897,000 股 / 742,675,370,680 元；
#   相加 = 30,241,914,000 股 / 3,461,558,839,660 元（詳見回報抽核）。
#
# 來源 3（口徑已實測驗證——與原始假設不同，這是關鍵發現）：不限用途款項借貸餘額
#   = TWSE TWTA1U 加總 6 個 selectType 類別（X=上市櫃可融資融券、A=不可融資融券、
#   F=基金受益憑證、G=黃金現貨、B=債券、I=應收在途交割款債權）。
#   實測發現：**單次呼叫只回傳其中一個類別**（selectType 預設值 X），並非一次涵蓋全部
#   6 類；6 類別的欄位配置（groups/fields）彼此不同（X/A 類含「融資」群組、span=7 含
#   「更換」欄；F/G/B/I 無融資群組、span=6 無「更換」欄），因此本程式**不假設固定欄位
#   位置**，改為每次呼又都從回傳的 `groups` metadata 動態定位「證券商不限用途款項借貸」
#   群組區間，再於該區間的 `fields` 找「今日餘額」欄的實際 index，逐列加總、6 類別加總後
#   才是全市場口徑。6 個 selectType 值取自 TWSE 網頁 <select name="selectType"> 的
#   option value（已用瀏覽器 JS 讀取確認：X/A/F/G/B/I）。單位仟股（頁面 hints 標示）。
#
# ════════════════════════════════════════════════════════════════
# daily / monthly 陣列排序方向
# ════════════════════════════════════════════════════════════════
#   沿用 taiwan-flows/data/foreign_history.json 實際檔案觀察到的方向：**舊到新（ascending）**。
#   該檔 monthly 為 dict，鍵值插入順序 2024-01 → 2026-07（舊到新）；daily 亦為 dict，
#   插入順序 2026-05-29 → 2026-07-13（舊到新）——皆由 `sorted(all_dates)` 升序建置而來。
#   本檔 daily/monthly 為 array（schema 鎖定），同樣採**升序**（daily[0]/monthly[0] 最舊，
#   daily[-1]/monthly[-1] 最新）。下一階段前端 agent 请依此升序寫聚合程式碼。
#
# ════════════════════════════════════════════════════════════════
# 兩種執行模式
# ════════════════════════════════════════════════════════════════
#   預設「每日模式」：抓當日四項，冪等 upsert 進 daily（保留近 30 交易日）；同時無條件
#     upsert 當月的 monthly[ym]（用當日值覆蓋），這樣月底最後一次執行後就會自然「凍結」
#     在該月最後交易日的值——不需要額外判斷「今天是不是月底」。
#   `--backfill`：一次性回補。FinMind 直接查 3 年範圍取得完整交易日曆（此 dataset 每個
#     交易日必有資料，可信賴當日曆用）；月底值＝每月最後一個交易日；daily 取最近 30 個
#     交易日。TWSE 兩項只對「近 30 日 ∪ 36 個月底」的聯集日期各抓一次（避免重複抓打兩次
#     API），日期層級用 ThreadPoolExecutor 併發抓 8 個子請求（2 借券 selectType + 6
#     不限用途 selectType）加速。
#
#   單一來源當日抓取失敗 → 重試一次仍失敗 → 該欄位填 null，不整批放棄。
#   FINMIND_TOKEN 全程走環境變數，不硬編金鑰；若環境無 token，每日模式仍可用 TWSE
#   回退（見 resolve_target_date）跑出 TWSE 兩項，FinMind 兩項該日填 null。

from __future__ import annotations

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from fmclient import TAIPEI, api_get, token  # noqa: E402 — 同目錄共用模組

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_mktbal")

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "market_balance_history.json"

TPE = TAIPEI  # 沿用本檔既有名稱
TWT72U_URL = "https://www.twse.com.tw/rwd/zh/lending/TWT72U"
TWTA1U_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/TWTA1U"

RECENT_DAILY_KEEP = 30
MONTHLY_KEEP = 36
BACKFILL_YEARS = 3
UNRESTRICTED_CATEGORIES = ["X", "A", "F", "G", "B", "I"]  # TWTA1U selectType 全 6 類
UNRESTRICTED_GROUP_TITLE = "證券商不限用途款項借貸"

# TWSE 節流＋指數退避重試：節流鎖 2026-07-24 抽到 twseclient.py（與 build_postmkt 共用），
# 退避迴圈仍在本檔各 fetcher（遇限流/空回應依 TWSE_RETRY_BACKOFFS 重試而非只一次）。
from twseclient import RETRY_BACKOFFS as TWSE_RETRY_BACKOFFS  # noqa: E402
from twseclient import throttled_get as _twse_throttled_get  # noqa: E402


def parse_num(s) -> int | None:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "　"):
        return 0
    try:
        return int(float(s))
    except ValueError:
        return None


# ════════════════════════════════════════════════════════════════
# 來源 1：FinMind 融資融券
# ════════════════════════════════════════════════════════════════

def fm_margin_range(start_date: str, end_date: str) -> dict[str, dict]:
    """回傳 {date: {margin_shares, margin_money, short_shares}}。單次查詢整段區間。
    失敗吞掉回 {}（FinMind 兩項該日填 null 的降級語意，重試已在 fmclient.api_get）。"""
    if not token():
        logger.warning("無 FINMIND_TOKEN，略過 FinMind 融資融券查詢")
        return {}
    try:
        rows = api_get("TaiwanStockTotalMarginPurchaseShortSale",
                       start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning(f"FinMind 融資融券查詢失敗：{e}")
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        d = row.get("date")
        name = row.get("name")
        rec = out.setdefault(d, {"margin_shares": None, "margin_money": None, "short_shares": None})
        if name == "MarginPurchase":
            rec["margin_shares"] = row.get("TodayBalance")
        elif name == "MarginPurchaseMoney":
            rec["margin_money"] = row.get("TodayBalance")
        elif name == "ShortSale":
            rec["short_shares"] = row.get("TodayBalance")
    logger.info(f"FinMind 融資融券 {start_date}~{end_date}：{len(out)} 交易日")
    return out


# ════════════════════════════════════════════════════════════════
# 來源 2：TWSE TWT72U 借券賣出餘額（SLB + NLB 整體市場合計相加）
# ════════════════════════════════════════════════════════════════

def _fetch_twt72u_total(date_iso: str, select_type: str) -> tuple[int, int] | None:
    """回傳該 selectType 的（整體市場 本日餘額股, 餘額市值元）；失敗回 None。"""
    date8 = date_iso.replace("-", "")
    max_attempts = len(TWSE_RETRY_BACKOFFS) + 1
    for attempt in range(max_attempts):
        try:
            r = _twse_throttled_get(TWT72U_URL, {"date": date8, "selectType": select_type, "response": "json"},
                                     timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("stat") != "OK":
                return None  # 非交易日/無資料（正常回應，非限流，不重試）
            for row in j.get("data") or []:
                if row and row[0] == "合計" and row[-1] == "整體市場":
                    shares = parse_num(row[5])
                    value = parse_num(row[7])
                    if shares is None or value is None:
                        return None
                    return shares, value
            return None  # 找不到整體市場列
        except Exception as e:
            if attempt < len(TWSE_RETRY_BACKOFFS):
                wait = TWSE_RETRY_BACKOFFS[attempt]
                logger.warning(f"TWT72U {select_type} {date_iso} 抓取失敗（第{attempt+1}/{max_attempts}次，"
                                f"{wait}s後重試）：{e}")
                time.sleep(wait)
            else:
                logger.warning(f"TWT72U {select_type} {date_iso} 抓取失敗（已重試{max_attempts}次，放棄）：{e}")
    return None


def twse_sbl_total(date_iso: str) -> tuple[int | None, int | None]:
    """借券賣出餘額 = SLB 整體市場 + NLB 整體市場（股／元）。任一失敗則整組回 None。"""
    with ThreadPoolExecutor(max_workers=2) as ex:
        slb_f = ex.submit(_fetch_twt72u_total, date_iso, "SLB")
        nlb_f = ex.submit(_fetch_twt72u_total, date_iso, "NLB")
        slb, nlb = slb_f.result(), nlb_f.result()
    if slb is None or nlb is None:
        return None, None
    return slb[0] + nlb[0], slb[1] + nlb[1]


# ════════════════════════════════════════════════════════════════
# 來源 3：TWSE TWTA1U 不限用途款項借貸餘額（6 類別加總）
# ════════════════════════════════════════════════════════════════

def _fetch_twta1u_category_total(date_iso: str, select_type: str) -> int | None:
    """回傳該類別「證券商不限用途款項借貸／今日餘額」欄逐列加總（仟股）；失敗回 None。"""
    date8 = date_iso.replace("-", "")
    max_attempts = len(TWSE_RETRY_BACKOFFS) + 1
    for attempt in range(max_attempts):
        try:
            r = _twse_throttled_get(TWTA1U_URL, {"date": date8, "response": "json", "selectType": select_type},
                                     timeout=45)
            r.raise_for_status()
            j = r.json()
            if j.get("stat") != "OK":
                return 0  # 該類別當日無資料（如非交易日），視為 0 貢獻而非整體失敗
            fields = j.get("fields") or []
            groups = j.get("groups") or []
            grp = next((g for g in groups if g.get("title") == UNRESTRICTED_GROUP_TITLE), None)
            if grp is None:
                logger.warning(f"TWTA1U {select_type} {date_iso}：找不到「{UNRESTRICTED_GROUP_TITLE}」群組")
                return None
            start, span = grp["start"], grp["span"]
            local = fields[start:start + span]
            if "今日餘額" not in local:
                logger.warning(f"TWTA1U {select_type} {date_iso}：群組內找不到「今日餘額」欄")
                return None
            idx = start + local.index("今日餘額")
            total = 0
            for row in j.get("data") or []:
                if not row or len(row) <= idx:
                    continue
                v = parse_num(row[idx])
                if v is not None:
                    total += v
            return total
        except Exception as e:
            if attempt < len(TWSE_RETRY_BACKOFFS):
                wait = TWSE_RETRY_BACKOFFS[attempt]
                logger.warning(f"TWTA1U {select_type} {date_iso} 抓取失敗（第{attempt+1}/{max_attempts}次，"
                                f"{wait}s後重試）：{e}")
                time.sleep(wait)
            else:
                logger.warning(f"TWTA1U {select_type} {date_iso} 抓取失敗（已重試{max_attempts}次，放棄）：{e}")
    return None


def twse_unrestricted_total(date_iso: str) -> int | None:
    """6 類別（X/A/F/G/B/I）今日餘額加總。任一類別重試後仍失敗 → 記警告但仍加總其餘
    成功類別（不因單一小類別瞬斷而整日報 null；X/B 兩大類是主要成分，若這兩者任一失敗
    才視為整體不可信）。"""
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = dict(zip(UNRESTRICTED_CATEGORIES,
                            ex.map(lambda c: _fetch_twta1u_category_total(date_iso, c), UNRESTRICTED_CATEGORIES)))
    if results.get("X") is None or results.get("B") is None:
        logger.warning(f"TWTA1U {date_iso}：主要類別 X/B 有缺，該日 unrestricted_shares 記 null")
        return None
    total = 0
    for c, v in results.items():
        if v is None:
            logger.warning(f"TWTA1U {date_iso} 類別 {c} 失敗，以 0 計入（次要類別）")
            continue
        total += v
    return total


# ════════════════════════════════════════════════════════════════
# 交易日解析（daily 模式用；無 token 時回退用 TWSE 探測）
# ════════════════════════════════════════════════════════════════

def resolve_target_date() -> str:
    tok = token()
    today = datetime.now(TPE).date()
    if tok:
        start = (today - timedelta(days=10)).isoformat()
        end = today.isoformat()
        data = fm_margin_range(start, end)
        if data:
            return max(data.keys())
        logger.warning("FinMind 近10日無資料，改用 TWSE 回退探測交易日")
    for back in range(10):
        d = today - timedelta(days=back)
        if _fetch_twt72u_total(d.isoformat(), "SLB") is not None:
            return d.isoformat()
    raise RuntimeError("近10日皆探測不到有效交易日（FinMind 與 TWSE 皆無資料）")


# ════════════════════════════════════════════════════════════════
# 組裝單日紀錄
# ════════════════════════════════════════════════════════════════

def build_record(date_iso: str, margin: dict | None) -> dict:
    sbl_shares, sbl_value = twse_sbl_total(date_iso)
    unrestricted = twse_unrestricted_total(date_iso)
    m = margin or {}
    return {
        "date": date_iso,
        "margin_shares": m.get("margin_shares"),
        "margin_money": m.get("margin_money"),
        "short_shares": m.get("short_shares"),
        "sbl_short_shares": sbl_shares,
        "sbl_short_value": sbl_value,
        "unrestricted_shares": unrestricted,
    }


FIELDS = ["margin_shares", "margin_money", "short_shares", "sbl_short_shares", "sbl_short_value", "unrestricted_shares"]


# ════════════════════════════════════════════════════════════════
# 每日模式
# ════════════════════════════════════════════════════════════════

def run_daily() -> dict:
    doc = load_existing()
    date_iso = resolve_target_date()
    logger.info(f"每日模式：目標交易日 {date_iso}")
    margin_map = fm_margin_range(date_iso, date_iso)
    rec = build_record(date_iso, margin_map.get(date_iso))

    daily_by_date = {d["date"]: d for d in doc["daily"]}
    daily_by_date[date_iso] = rec
    daily_sorted = sorted(daily_by_date.values(), key=lambda r: r["date"])[-RECENT_DAILY_KEEP:]

    ym = date_iso[:7]
    monthly_by_ym = {m["ym"]: m for m in doc["monthly"]}
    monthly_by_ym[ym] = {"ym": ym, "date": date_iso, **{k: rec[k] for k in FIELDS}}
    monthly_sorted = sorted(monthly_by_ym.values(), key=lambda r: r["ym"])[-MONTHLY_KEEP:]

    return finalize(daily_sorted, monthly_sorted)


# ════════════════════════════════════════════════════════════════
# Backfill 模式
# ════════════════════════════════════════════════════════════════

def run_backfill() -> dict:
    today = datetime.now(TPE).date()
    start = today.replace(year=today.year - BACKFILL_YEARS).isoformat()
    margin_all = fm_margin_range(start, today.isoformat())
    trading_dates = sorted(margin_all.keys())
    if not trading_dates:
        raise RuntimeError("FinMind 3年範圍查無交易日資料（無 FINMIND_TOKEN 或 API 失敗），backfill 需要 token 才能建立交易日曆")

    recent_dates = trading_dates[-RECENT_DAILY_KEEP:]

    by_ym: dict[str, str] = {}
    for d in trading_dates:
        ym = d[:7]
        if ym not in by_ym or d > by_ym[ym]:
            by_ym[ym] = d
    ym_sorted = sorted(by_ym.keys())[-MONTHLY_KEEP:]
    month_end_dates = [by_ym[ym] for ym in ym_sorted]

    union_dates = sorted(set(recent_dates) | set(month_end_dates))
    logger.info(f"backfill：近{len(recent_dates)}交易日 ∪ {len(month_end_dates)}個月底 = 聯集{len(union_dates)}日需查 TWSE")

    records: dict[str, dict] = {}
    for i, d in enumerate(union_dates):
        rec = build_record(d, margin_all.get(d))
        records[d] = rec
        logger.info(f"  [{i+1}/{len(union_dates)}] {d} 完成："
                    f"margin={rec['margin_shares']} sbl_shares={rec['sbl_short_shares']} unrestricted={rec['unrestricted_shares']}")

    daily_sorted = [records[d] for d in recent_dates]
    monthly_sorted = [{"ym": ym, "date": by_ym[ym], **{k: records[by_ym[ym]][k] for k in FIELDS}} for ym in ym_sorted]

    return finalize(daily_sorted, monthly_sorted)


# ════════════════════════════════════════════════════════════════
# I/O
# ════════════════════════════════════════════════════════════════

def load_existing() -> dict:
    if OUT_PATH.exists():
        try:
            j = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            return {"daily": j.get("daily", []), "monthly": j.get("monthly", [])}
        except Exception as e:
            logger.warning(f"讀取既有 {OUT_PATH} 失敗（將視為空白重建）：{e}")
    return {"daily": [], "monthly": []}


def finalize(daily: list[dict], monthly: list[dict]) -> dict:
    latest_date = daily[-1]["date"] if daily else (monthly[-1]["date"] if monthly else None)
    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "latest_date": latest_date,
        "units": {
            "margin_shares": "張", "margin_money": "元", "short_shares": "張",
            "sbl_short_shares": "股", "sbl_short_value": "元", "unrestricted_shares": "仟股",
        },
        "daily": daily,
        "monthly": monthly,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="一次性回補近3年月底值＋近30交易日")
    args = ap.parse_args()

    doc = run_backfill() if args.backfill else run_daily()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_kb = OUT_PATH.stat().st_size / 1024
    logger.info(f"已寫入 {OUT_PATH}：daily {len(doc['daily'])} 筆、monthly {len(doc['monthly'])} 筆、"
                f"latest_date={doc['latest_date']}、檔案大小 {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
