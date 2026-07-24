# build_postmkt.py — 盤後分析資料管線
#
# 抓 FinMind 5 個盤後 dataset（全市場、單日），各 tab 預先聚合/排序，
# 輸出單一 data/postmkt.json 給前端（index.html）讀取。
# token 讀環境變數 FINMIND_TOKEN（GitHub Actions 由 secrets 帶入）。
#
# 排行榜每表取前 50 檔；鉅額交易本質稀疏，保留全量。

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from fmclient import TAIPEI, api_get, token  # noqa: E402
from twseclient import throttled_get  # noqa: E402 — TWSE 全域節流（限流教訓見該檔檔頭）

TOP_N = 50
MAX_BACK_DAYS = 5


def fetch_latest(dataset: str, base_date: dt.date) -> tuple[str, list]:
    """從 base_date 起往前回退最多 MAX_BACK_DAYS 天，取第一個有資料的交易日（全市場查詢）。"""
    for back in range(MAX_BACK_DAYS + 1):
        d = (base_date - dt.timedelta(days=back)).isoformat()
        rows = api_get(dataset, start_date=d, end_date=d)
        if rows:
            print(f"  {dataset}: {d} -> {len(rows)} 筆")
            return d, rows
    print(f"  {dataset}: {base_date} 起回退 {MAX_BACK_DAYS} 天皆無資料")
    return "", []


def fetch_daytrading(base_date: dt.date) -> tuple[str, list, list]:
    """當沖需已 settle 的 Volume（約 21:30 後），且要有同日 TaiwanStockPrice 當比重分母。
    兩者任一缺就往前回退（當沖標的清單盤前就有、但 Volume 全空，不能算有資料）。"""
    for back in range(MAX_BACK_DAYS + 1):
        d = (base_date - dt.timedelta(days=back)).isoformat()
        rows = api_get("TaiwanStockDayTrading", start_date=d, end_date=d)
        if not any((r.get("Volume") or 0) > 0 for r in rows):
            continue
        price = api_get("TaiwanStockPrice", start_date=d, end_date=d)
        if not price:
            continue
        print(f"  TaiwanStockDayTrading: {d} -> {len(rows)} 筆；TaiwanStockPrice -> {len(price)} 筆")
        return d, rows, price
    print(f"  TaiwanStockDayTrading: {base_date} 起回退 {MAX_BACK_DAYS} 天皆無已結算資料")
    return "", [], []


def fetch_twse_lending(date: str, select_type: str) -> dict:
    """TWSE公開JSON端點（免金鑰）：借券系統(SLB)/證商營業處所(NLB)借券餘額表，全市場單次查詢。
    見 docs/cmoney-sbl-mapping-research.md 2.2節，date=YYYY-MM-DD可直接傳、TWSE自動轉換。
    此端點無正式SLA保證，失敗時回空dict，呼叫端不應讓整個管線因此中斷。
    重試一次（間隔5秒）：實測過暫時性失敗讓整批sys_bal歸零、排行整個變形，
    多一次重試能擋掉大部分瞬斷。"""
    j = None
    for attempt in range(2):
        try:
            r = throttled_get("https://www.twse.com.tw/rwd/zh/lending/TWT72U",
                              params={"date": date.replace("-", ""), "selectType": select_type, "response": "json"},
                              timeout=30)
            r.raise_for_status()
            j = r.json()
            break
        except Exception as e:
            print(f"  TWSE {select_type} 抓取失敗（第{attempt+1}次）：{e}", flush=True)
            if attempt == 0:
                import time
                time.sleep(5)
    if j is None:
        print(f"  TWSE {select_type} 兩次皆失敗（該欄位缺值，不影響其餘欄位）", flush=True)
        return {}
    def num(s):
        try:
            return float(str(s).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    out = {}
    for row in j.get("data") or []:
        if not row or not row[0]:
            continue
        c = str(row[0]).strip()
        if not c.isascii() or not c.isalnum():
            continue  # 排除「合計」市場總計列（代號欄是中文，非真實股票代號）
        # 欄位順序：代號/名稱/前日餘額/當日借/當日還/當日餘額/收盤價/當日餘額市值/註記
        # 原始單位：股／元，統一轉張／千元，跟其餘 dataset 的單位一致
        out[c] = {"prev": num(row[2]) / 1000, "in": num(row[3]) / 1000, "out": num(row[4]) / 1000,
                  "bal": num(row[5]) / 1000, "mv": num(row[7]) / 1000}
    return out


def fetch_twse_oddlot(base_date: dt.date, report: str) -> tuple[str, list]:
    """TWSE 零股行情單公開端點（免金鑰）：TWTC7U=盤中零股、TWT53U=盤後零股。
    兩者都在 rwd/zh/afterTrading/ 下、支援 date 參數查歷史；從 base_date 往前
    回退找有資料的交易日（同 fetch_latest 的回退邏輯）。無正式SLA，失敗回空。"""
    for back in range(MAX_BACK_DAYS + 1):
        d = base_date - dt.timedelta(days=back)
        try:
            r = throttled_get(f"https://www.twse.com.tw/rwd/zh/afterTrading/{report}",
                              params={"date": d.strftime("%Y%m%d"), "response": "json"}, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            print(f"  TWSE {report} {d} 抓取失敗：{e}", flush=True)
            continue
        if j.get("stat") == "OK" and j.get("data"):
            print(f"  TWSE {report}: {d} -> {len(j['data'])} 筆")
            return d.isoformat(), j["data"]
    print(f"  TWSE {report}: {base_date} 起回退 {MAX_BACK_DAYS} 天皆無資料")
    return "", []


def build_oddlot(date_intra: str, rows_intra: list, date_after: str, rows_after: list) -> dict:
    """零股tab：盤中(TWTC7U)/盤後(TWT53U)，皆取前5欄=代號/名稱/成交股數/筆數/金額。
    盤中版多的四個價格欄不取（規格只要股數/筆數/金額）。只留成交股數>0，
    預設依金額排序（前端表頭仍可再排序）。單位：股／元（原始單位，不轉換）。"""
    def parse(rows):
        out = []
        for row in rows:
            c = str(row[0] or "").strip()
            if not c.isascii() or not c.isalnum():
                continue  # 排除「合計」市場總計列（代號欄空白/中文，同TWT72U的已知坑）
            def num(i):
                try:
                    return float(str(row[i]).replace(",", ""))
                except (TypeError, ValueError):
                    return 0.0
            sh = num(2)
            if sh <= 0:
                continue
            out.append({"c": c, "n": str(row[1]).strip(),
                        "sh": round(sh), "deals": round(num(3)), "amt": round(num(4))})
        out.sort(key=lambda x: -x["amt"])
        return out
    return {"intraday": {"date": date_intra, "rows": parse(rows_intra)},
            "after": {"date": date_after, "rows": parse(rows_after)}}


def build_traders(trade_date: str) -> dict:
    """分點tab「清單」：全部券商分點代號/名稱/地址（TaiwanSecuritiesTraderInfo，Free）。
    date 給前端「個股/單點」互動查詢當預設查詢日（TradingDailyReport每交易日21:00更新）。"""
    rows = api_get("TaiwanSecuritiesTraderInfo")
    out = [{"id": r.get("securities_trader_id") or "", "name": r.get("securities_trader") or "",
            "addr": r.get("address") or ""} for r in rows]
    out.sort(key=lambda x: x["id"])
    print(f"  TaiwanSecuritiesTraderInfo: {len(out)} 分點")
    return {"date": trade_date, "list": out}


def stock_names() -> dict:
    """TaiwanStockInfo 建 code -> 中文名 對照（同一 code 取第一筆）。"""
    rows = api_get("TaiwanStockInfo")
    names = {}
    for r in rows:
        c = r.get("stock_id")
        if c and c not in names:
            names[c] = r.get("stock_name") or ""
    print(f"  TaiwanStockInfo: {len(names)} 檔對照")
    return names


# ---------- 各 tab 聚合 ----------

def build_margin(date: str, rows: list, nm: dict) -> dict:
    """融資：餘額增減排行（今日-昨日，張）＋融資使用率排行（餘額/限額）。"""
    recs = []
    for r in rows:
        bal = r.get("MarginPurchaseTodayBalance") or 0
        prev = r.get("MarginPurchaseYesterdayBalance") or 0
        limit = r.get("MarginPurchaseLimit") or 0
        c = r.get("stock_id", "")
        recs.append({
            "c": c, "n": nm.get(c, ""),
            "bal": bal, "chg": bal - prev, "limit": limit,
            "usage": round(bal / limit * 100, 2) if limit > 0 else None,
        })
    inc = sorted((x for x in recs if x["chg"] > 0), key=lambda x: -x["chg"])[:TOP_N]
    dec = sorted((x for x in recs if x["chg"] < 0), key=lambda x: x["chg"])[:TOP_N]
    usage = sorted((x for x in recs if x["usage"] is not None and x["bal"] > 0),
                   key=lambda x: -x["usage"])[:TOP_N]
    return {"date": date, "increase": inc, "decrease": dec, "usage": usage}


def build_lending(date: str, lend_rows: list, margin_rows: list, short_rows: list,
                   dt_rows: list, dt_date: str, price_rows: list, inst_rows: list,
                   hold_rows: list, nm: dict) -> dict:
    """借券 tab：取代CMoney SBL.xlsx的Table1(單股詳細)+Table2(多股排行)。
    見 docs/cmoney-sbl-mapping-research.md——整合8個FinMind dataset+TWSE兩平台
    借券餘額端點，唯一缺的3類官方未公開資料（TSE還券完成明細、投信/自營商
    持股水位、個股層級維持率）不做（自行推估容易誤導，見研究文件2.3節）。
    """
    close = {r.get("stock_id"): r.get("close") for r in price_rows if r.get("close") is not None}

    # TWSE 兩平台借券餘額（SLB=借券系統, NLB=證商營業處所），全市場各一次查詢
    sys_bal = fetch_twse_lending(date, "SLB") if date else {}
    otc_bal = fetch_twse_lending(date, "NLB") if date else {}

    # 借券成交明細（逐筆）→ 依股票彙總當日成交量/筆數/最高費率
    lend_agg: dict[str, dict] = {}
    for r in lend_rows:
        c = r.get("stock_id", "")
        o = lend_agg.setdefault(c, {"vol": 0, "deals": 0, "fee_max": None})
        o["vol"] += r.get("volume") or 0
        o["deals"] += 1
        fr = r.get("fee_rate")
        if fr is not None and (o["fee_max"] is None or fr > o["fee_max"]):
            o["fee_max"] = fr

    # 融資融券（融資買賣餘額 + 融券放空、券商營業處所借券賣出SBL流量+餘額都在同一個dataset）
    margin_by_c = {r.get("stock_id"): r for r in margin_rows}
    short_by_c = {r.get("stock_id"): r for r in short_rows}

    # 三大法人買賣超金額：dataset給股數，乘收盤價換算成金額（千元）
    inst_by_c: dict[str, dict] = {}
    for r in inst_rows:
        c = r.get("stock_id", "")
        o = inst_by_c.setdefault(c, {"foreign": 0, "trust": 0, "dealer": 0})
        net = (r.get("buy") or 0) - (r.get("sell") or 0)
        name = r.get("name")
        if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
            o["foreign"] += net
        elif name == "Investment_Trust":
            o["trust"] += net
        elif name in ("Dealer_self", "Dealer_Hedging", "Dealer"):
            o["dealer"] += net

    hold_by_c = {r.get("stock_id"): r for r in hold_rows}

    codes = set(sys_bal) | set(otc_bal) | set(lend_agg) | set(margin_by_c) | set(short_by_c)
    rows_out = []
    for c in codes:
        px = close.get(c)
        sb, ob = sys_bal.get(c, {}), otc_bal.get(c, {})
        m, s = margin_by_c.get(c, {}), short_by_c.get(c, {})
        it = inst_by_c.get(c, {"foreign": 0, "trust": 0, "dealer": 0})
        hd = hold_by_c.get(c, {})
        la = lend_agg.get(c, {})

        sys_bal_v, otc_bal_v = sb.get("bal", 0), ob.get("bal", 0)
        plat_total = sys_bal_v + otc_bal_v
        sys_chg = round(sb.get("in", 0) - sb.get("out", 0))
        otc_chg = round(ob.get("in", 0) - ob.get("out", 0))
        sbl_short = (s.get("SBLShortSalesCurrentDayBalance") or 0) / 1000
        sbl_short_prev = (s.get("SBLShortSalesPreviousDayBalance") or 0) / 1000
        margin_short = (s.get("MarginShortSalesCurrentDayBalance") or 0) / 1000
        margin_short_prev = (s.get("MarginShortSalesPreviousDayBalance") or 0) / 1000
        margin_bal = m.get("MarginPurchaseTodayBalance") or 0
        margin_limit = m.get("MarginPurchaseLimit") or 0
        short_limit = (s.get("MarginShortSalesQuota") or 0) / 1000
        margin_chg = round(margin_bal - (m.get("MarginPurchaseYesterdayBalance") or 0))
        sbl_short_chg_v = round(sbl_short - sbl_short_prev)
        margin_short_chg_v = round(margin_short - margin_short_prev)

        # 瘦身（2026-07-24）：市值/金額衍生欄（*_mv_chg、*_net、plat_total*、short_total*、
        # foreign_shares_mv 等）不再落地——一律可由「基礎量 × px（當日收盤價）」重建，
        # 由消費端現算：前端 index.html augmentLending()、摘要管線 build_summary.py
        # _augment_lending()，公式必須與原後端版一致。由捨入後基礎量重建與原值最多差
        # ±0.5張×px（千元級），遠小於顯示精度（億、1-2位小數）。省 ~0.8MB/2.4MB。
        # sys_mv/otc_mv 例外保留：TWSE 官方市值欄（官方收盤口徑），非本管線推算。
        row = {
            "c": c, "n": nm.get(c, ""), "px": px,
            # 兩平台借券餘額（張；fetch_twse_lending()已將股轉張、元轉千元）
            "sys_bal": round(sys_bal_v), "sys_chg": sys_chg, "sys_mv": round(sb.get("mv", 0)),
            "otc_bal": round(otc_bal_v), "otc_chg": otc_chg, "otc_mv": round(ob.get("mv", 0)),
            # Short interest（放空部位：借賣＝SBL借券賣出、融券）
            "sbl_short_bal": round(sbl_short), "sbl_short_chg": sbl_short_chg_v,
            "margin_short_bal": round(margin_short), "margin_short_chg": margin_short_chg_v,
            "usage_ratio": round(sbl_short / plat_total * 100, 2) if plat_total > 0 else None,
            # 借券賣出SBL流量明細（千元/張）
            "sbl_sales": round((s.get("SBLShortSalesShortSales") or 0) / 1000),
            "sbl_returns": round((s.get("SBLShortSalesReturns") or 0) / 1000),
            # 借券成交明細彙總（今日TSE）
            "lend_vol": la.get("vol", 0), "lend_deals": la.get("deals", 0), "lend_fee_max": la.get("fee_max"),
            # 融資
            "margin_buy": m.get("MarginPurchaseBuy"), "margin_bal": round(margin_bal),
            "margin_chg": margin_chg,
            "margin_usage": round(margin_bal / margin_limit * 100, 2) if margin_limit > 0 else None,
            "short_usage": round(margin_short / short_limit * 100, 2) if short_limit > 0 else None,
            "credit_ratio": round(margin_short / margin_bal * 100, 2) if margin_bal > 0 else None,
            "offset": m.get("OffsetLoanAndShort"),
            # 當沖（由呼叫端合併，避免這裡重複算比重分母）
            # 三大法人買賣超（張數：股數差÷1000；金額衍生欄由消費端 vol×px 重建）
            "foreign_vol": round(it["foreign"] / 1000),
            "trust_vol": round(it["trust"] / 1000),
            "dealer_vol": round(it["dealer"] / 1000),
            # 外資持股
            "foreign_shares": hd.get("ForeignInvestmentShares"),
            "foreign_ratio": hd.get("ForeignInvestmentSharesRatio"),
            "foreign_remain_ratio": hd.get("ForeignInvestmentRemainRatio"),
            "foreign_limit_ratio": hd.get("ForeignInvestmentUpperLimitRatio"),
        }
        rows_out.append(row)

    # 當沖欄位只在資料日與借券基準日一致時合併（寧缺勿混，2026-07-14 S1 修正）：
    # fetch_daytrading 有自己的回退邏輯，可能退到比基準日更早的交易日，直接合併
    # 會把不同天的當沖量錯配進今日借券列且無任何警告。日期不一致時 dt_* 一律留
    # None；當沖 tab 本身不受影響（它有自己的 date 欄與下游 dlabel 保護）。
    if dt_date and date and dt_date != date:
        print(f"  ⚠ 借券tab：當沖資料日 {dt_date} ≠ 基準日 {date}，dt_* 欄一律留空（寧缺勿混）", flush=True)
        dt_by_c = {}
    else:
        dt_by_c = {r.get("stock_id"): r for r in dt_rows}
    for row in rows_out:
        d = dt_by_c.get(row["c"])
        if d:
            buy_amt = round((d.get("BuyAmount") or 0) / 1000)
            sell_amt = round((d.get("SellAmount") or 0) / 1000)
            row["dt_vol"] = d.get("Volume") or 0
            row["dt_amt"] = round((buy_amt + sell_amt) / 2)
            row["dt_diff"] = sell_amt - buy_amt
        else:
            row["dt_vol"] = row["dt_amt"] = row["dt_diff"] = None

    rows_out.sort(key=lambda x: -(x["sys_bal"] + x["otc_bal"]))  # 依兩平台借券餘額合計
    # 不截斷到TOP_N：Table1的定位是「查任一檔股票」，前端主排行榜只顯示前TOP_N，
    # 但完整清單要留給搜尋功能查詢不在前段班的股票（見cmoney-sbl-mapping-research.md）。
    return {"date": date, "rows": rows_out,
            "sys_available": bool(sys_bal), "otc_available": bool(otc_bal)}


def build_short_balance(date: str, rows: list, nm: dict) -> dict:
    """融券餘額增減 + 借券賣出餘額增減 兩張排行（原始單位為股，轉成張）。"""
    ms, sbl = [], []
    for r in rows:
        c = r.get("stock_id", "")
        n = nm.get(c, "")
        m_bal = (r.get("MarginShortSalesCurrentDayBalance") or 0) / 1000
        m_chg = m_bal - (r.get("MarginShortSalesPreviousDayBalance") or 0) / 1000
        s_bal = (r.get("SBLShortSalesCurrentDayBalance") or 0) / 1000
        s_chg = s_bal - (r.get("SBLShortSalesPreviousDayBalance") or 0) / 1000
        if m_chg:
            ms.append({"c": c, "n": n, "bal": round(m_bal), "chg": round(m_chg)})
        if s_chg:
            sbl.append({"c": c, "n": n, "bal": round(s_bal), "chg": round(s_chg)})
    ms.sort(key=lambda x: -abs(x["chg"]))
    sbl.sort(key=lambda x: -abs(x["chg"]))
    return {"date": date, "margin_short": ms[:TOP_N], "sbl": sbl[:TOP_N]}


def daytrading_broker_estimate(date: str, codes: list, close_map: dict, top_k: int = 3) -> dict:
    """當沖無逐筆券商分點資料（TaiwanStockDayTrading只有個股加總）。用
    TaiwanStockTradingDailyReport（各分點逐價位買賣量）估算：同分點當日
    買量/賣量取較小值 = 該分點「有能力形成當沖」的量，是分析圈常見的
    分點當沖貢獻度估算法，非逐筆交易的直接證據（可能是不同客戶各自
    單向交易剛好量相近）。每檔一次API call，逐檔失敗不影響其他檔。
    金額（千元）以當日收盤價估算張數對應金額，非分點實際成交均價
    （該dataset沒有逐價位對應的分點均價可用）。"""
    out = {}
    for c in codes:
        try:
            rows = api_get("TaiwanStockTradingDailyReport", data_id=c, start_date=date, end_date=date)
        except Exception as e:
            print(f"  分點資料抓取失敗 {c}：{e}", flush=True)
            continue
        agg: dict[str, list] = {}
        for r in rows:
            t = r.get("securities_trader")
            if not t:
                continue
            a = agg.setdefault(t, [0, 0])
            a[0] += r.get("buy") or 0
            a[1] += r.get("sell") or 0
        px = close_map.get(c)
        est = [{"trader": t, "vol": round(min(b, s) / 1000),
                "money": round(min(b, s) / 1000 * px) if px else None}
               for t, (b, s) in agg.items() if min(b, s) > 0]
        est.sort(key=lambda x: -(x["money"] or 0))
        out[c] = est[:top_k]
    return out


def build_daytrading(date: str, rows: list, price_rows: list, nm: dict) -> dict:
    """當沖：金額排行＋當沖比重排行（分母 = 同日 TaiwanStockPrice 的 Trading_Volume）。"""
    tv = {r.get("stock_id"): (r.get("Trading_Volume") or 0) for r in price_rows}
    close_map = {r.get("stock_id"): r.get("close") for r in price_rows if r.get("close") is not None}
    px_map = {r.get("stock_id"): r for r in price_rows}
    recs = []
    for r in rows:
        vol = r.get("Volume") or 0
        if vol <= 0:
            continue
        c = r.get("stock_id", "")
        amt = ((r.get("BuyAmount") or 0) + (r.get("SellAmount") or 0)) / 2
        total = tv.get(c) or 0
        p = px_map.get(c, {})
        close, spread = p.get("close"), p.get("spread")
        prev_close = (close - spread) if (close is not None and spread is not None) else None
        chg_pct = round(spread / prev_close * 100, 2) if prev_close else None
        amp_pct = round((p.get("max") - p.get("min")) / prev_close * 100, 2) \
            if (prev_close and p.get("max") is not None and p.get("min") is not None) else None
        recs.append({
            "c": c, "n": nm.get(c, ""),
            "vol": vol, "amt": round(amt),
            "ratio": round(vol / total * 100, 2) if total > 0 else None,
            "chg_pct": chg_pct, "amp_pct": amp_pct,
        })
    by_amount = sorted(recs, key=lambda x: -x["amt"])[:TOP_N]
    # by_ratio（比重榜）2026-07-24 停產：前端與摘要管線只消費 by_amount，比重榜
    # 從未被渲染；停產同時把分點推估查詢從「兩榜聯集 ~100 檔」減半為金額榜 50 檔

    codes = sorted({x["c"] for x in by_amount})
    if date and codes:
        print(f"  券商分點推估：{len(codes)} 檔逐一查詢…", flush=True)
        brokers = daytrading_broker_estimate(date, codes, close_map)
        for x in by_amount:
            x["traders"] = brokers.get(x["c"], [])

    return {"date": date, "by_amount": by_amount}


def block_trader_map(date: str) -> dict:
    """TaiwanStockBlockTradingDailyReport：同一筆鉅額交易的買方/賣方券商分點各一列
    （buy=量代表買方、sell=量代表賣方），用(股票,價格)分組後嘗試唯一匹配對應
    TaiwanStockBlockTrade的每一筆量。只在買賣雙方各剛好一筆候選時採用，
    避免同股同價當天多筆交易時誤配（該dataset僅2026-04-28起有資料，之前日期查無）。
    """
    try:
        rows = api_get("TaiwanStockBlockTradingDailyReport", start_date=date, end_date=date)
    except Exception as e:
        print(f"券商分點資料抓取失敗（不影響其餘欄位）：{e}", flush=True)
        return {}
    by_sp: dict[tuple, list] = {}
    for r in rows:
        key = (r.get("stock_id"), r.get("price"))
        by_sp.setdefault(key, []).append(r)
    return by_sp


def build_blocktrade(date: str, rows: list, nm: dict) -> dict:
    """鉅額交易：依股票分組（組內依金額排序、組間依該股當日總金額排序），
    每列附上同股票當日總張數/總金額，並嘗試附上買方/賣方券商分點。"""
    trader_map = block_trader_map(date) if date else {}
    items = []
    for r in rows:
        c = r.get("stock_id", "")
        vol = r.get("volume") or 0
        price = r.get("price")
        buy_trader = sell_trader = None
        cands = trader_map.get((c, price), [])
        buyers = [x for x in cands if x.get("buy") == vol]
        sellers = [x for x in cands if x.get("sell") == vol]
        if len(buyers) == 1:
            buy_trader = buyers[0].get("securities_trader")
        if len(sellers) == 1:
            sell_trader = sellers[0].get("securities_trader")
        items.append({
            "c": c, "n": nm.get(c, ""),
            "type": r.get("trade_type", ""),
            "price": price,
            "vol": vol,
            "money": r.get("trading_money") or 0,
            "buy_trader": buy_trader, "sell_trader": sell_trader,
        })

    by_stock: dict[str, list] = {}
    for it in items:
        by_stock.setdefault(it["c"], []).append(it)
    groups = sorted(by_stock.values(), key=lambda g: -sum(x["money"] for x in g))
    out = []
    for g in groups:
        g.sort(key=lambda x: -x["money"])
        tv, tm = sum(x["vol"] for x in g), sum(x["money"] for x in g)
        for it in g:
            it["stock_vol"] = tv
            it["stock_money"] = tm
            out.append(it)
    return {"date": date, "rows": out}


def main() -> None:
    token(required=True)  # 本管線 token 必要；缺就在開跑前失敗，不要抓到一半才炸
    today = dt.date.today()
    print("抓取 FinMind 盤後資料…")
    nm = stock_names()

    d_margin, r_margin = fetch_latest("TaiwanStockMarginPurchaseShortSale", today)
    d_lend, r_lend = fetch_latest("TaiwanStockSecuritiesLending", today)
    d_short, r_short = fetch_latest("TaiwanDailyShortSaleBalances", today)
    d_dt, r_dt, r_price = fetch_daytrading(today)
    d_block, r_block = fetch_latest("TaiwanStockBlockTrade", today)
    d_inst, r_inst = fetch_latest("TaiwanStockInstitutionalInvestorsBuySell", today)
    d_hold, r_hold = fetch_latest("TaiwanStockShareholding", today)
    d_oddi, r_oddi = fetch_twse_oddlot(today, "TWTC7U")
    d_odda, r_odda = fetch_twse_oddlot(today, "TWT53U")

    dates = [d for d in (d_margin, d_lend, d_short, d_dt, d_block, d_inst, d_hold) if d]
    latest = max(dates) if dates else ""
    # 借券tab整合多個dataset，用短餘額表的日期當TWSE兩平台查詢基準（核心資料，
    # 各dataset正常應同一交易日；若當天TaiwanDailyShortSaleBalances缺，退用最新日期）。
    lend_date = d_short or latest
    # r_price（daytrading用）可能是不同日期，借券tab的金額換算要用同一天收盤價，另抓一次
    r_price_lend = api_get("TaiwanStockPrice", start_date=lend_date, end_date=lend_date) if lend_date else []
    # 借券tab混合多個dataset，若日期沒對齊會把不同天的資料錯配在同一列——只記警告不中斷，
    # 因為單日落後在同一批交易日內通常仍可用（比完全不出資料好），但要能被發現排查。
    mismatch = [{"name": n, "date": d} for n, d in (
        ("融資", d_margin), ("借券成交", d_lend), ("三大法人", d_inst),
        ("外資持股", d_hold), ("當沖", d_dt)) if d and lend_date and d != lend_date]
    if mismatch:
        print(f"  ⚠ 借券tab日期不對齊（基準{lend_date}）："
              f"{[m['name'] for m in mismatch]} 使用了不同日期的資料", flush=True)
    out = {
        "date": latest,
        "generated_at": dt.datetime.now(TAIPEI).isoformat(timespec="seconds"),
        # 借券tab多dataset日期落後偵測（P5）：非空＝有dataset落後於基準lend_date，前端頁首顯示徽章
        "date_mismatch": mismatch,
        "margin": build_margin(d_margin, r_margin, nm),
        "lending": build_lending(lend_date, r_lend, r_margin, r_short, r_dt, d_dt, r_price_lend, r_inst, r_hold, nm),
        "short_balance": build_short_balance(d_short, r_short, nm),
        "daytrading": build_daytrading(d_dt, r_dt, r_price, nm),
        "blocktrade": build_blocktrade(d_block, r_block, nm),
        "oddlot": build_oddlot(d_oddi, r_oddi, d_odda, r_odda),
        # 分點互動查詢（個股/單點）由前端直呼FinMind（TradingDailyReport 21:00更新，
        # 跟當沖同源），預設查詢日跟當沖對齊
        "brokers": build_traders(d_dt or latest),
    }

    dst = ROOT / "data" / "postmkt.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"輸出 {dst}（{dst.stat().st_size:,} bytes）")


if __name__ == "__main__":
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    main()
