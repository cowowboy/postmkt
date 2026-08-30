# build_summary.py — 「彙總分析」自動產出管線（GitHub Actions 排程用）
#
# ★★★ 本檔 gather 邏輯移植自三站前端 insightGatherContext()，前端改動時需同步此檔 ★★★
#   - 頁面1 盤後分析   ：postmkt/index.html            insightGatherContext + insightFetchBrokers
#   - 頁面2 即時類股動態：taiwan-flow-live-v2/index.html insightGatherContext（含 sval 欄位解碼）
#   - 頁面3 新聞晨報   ：taiwan-stock-news/index.html   insightGatherContext
# 三段 context 的段落結構、欄位語意、dlabel 跨日警告機制、千元→億換算、取前N
# 皆與 JS 版等價；各頁 SYS prompt 原樣取自三站前端，彙總 SYS 取自規格書。
#
# 流程：資料齊全閘門（--slot am|pm 輪詢；pm 硬等 postmkt/news晚班/taiwan-flows
# 三源皆今日，am 硬等 morning.json 後再軟等 us.json＋news早班，詳 wait_gate docstring）
# → 構建 3 段 context → Message Batches 提交 3 份摘要（3 context × Sonnet 5 各 1 次，
# 半價；超時/單筆失敗逐筆同步回退）→ ≥2 份成功才做 1 次 Opus 4.8 彙總（同樣 batch
# 優先、回退同步）→ 輸出 data/summary/YYYYMMDD-{am|pm}.json ＋ 重建 index.json ＋
# 刪 3 日前舊檔。batch 期限受全場時間預算折算（batch_deadline），不會撞 workflow timeout。
#
# 金鑰只讀環境變數（ANTHROPIC_API_KEY / FINMIND_TOKEN），絕不落檔、絕不 print。
# 旗標：--no-wait 跳過齊全閘門；--sync 全同步不走 batch（除錯）；
#       --dry-run 只印三段 context 不呼叫 Anthropic。

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from fmclient import TAIPEI, api_get, token as fm_token  # noqa: E402

OUT_DIR = ROOT / "data" / "summary"

# 帶普通瀏覽器 UA（同 src/build_screen.py）：預設 python-requests UA 曾在同族管線被
# Cloudflare 依 UA 全擋（taiwan-flow-live-v2 cards 管線 1010 事故），此為同族預防。
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ---------- 資料源 ----------
URL_POSTMKT = "https://raw.githubusercontent.com/shihpc/postmkt/main/data/postmkt.json"
URL_AETF_LATEST = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/aetf/latest.json"
URL_AETF_DIFF = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/aetf/diff.json"
URL_TF = "https://raw.githubusercontent.com/shihpc/taiwan-flows/main/data/latest.json"
URL_LIVE = "https://taiwan-flow-v2.shihpc.workers.dev/live"
URL_CLASSIFY = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/classify.json"
URL_NEWS = "https://raw.githubusercontent.com/shihpc/taiwan-stock-news/main/news.json"
URL_MORNING = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/morning.json"
URL_US = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/us.json"
URL_FINMIND_TDR = "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report"
URL_ANTHROPIC = "https://api.anthropic.com/v1/messages"

# 與 postmkt/index.html 的 INSIGHT_BROKERS / SUM_MODELS 對齊
INSIGHT_BROKERS = ["9268", "9800", "9600", "9A00"]
# 每頁 1 次 Sonnet 5 分析（2026-08-29 由每頁 2 次砍半——8 場 24 組 A/B 實證：標的重疊
# Jaccard≈0.5、方向零衝突、精華標的 98% 取自 A∩B，使用者裁定成本優先）；彙總維持 Opus 4.8
SUMMARY_MODELS = ["claude-sonnet-5"]
SYNTH_MODEL = "claude-opus-4-8"
MIN_OK_FOR_SYNTH = 2     # 3 份中至少幾份成功才彙總

# v2/index.html 的 SC：live.stocks 陣列欄位順序（live.stock_cols 缺時的後備）
SC = ["chg", "amt", "close", "vol", "bv", "sv", "pts", "dp", "lim", "lw",
      "f10", "c10", "c30", "r10", "it", "fi", "y1", "y2", "ints", "nl"]


# ---------- 小工具（對齊 JS 版格式化行為） ----------

def js_round(v: float) -> int:
    """JS Math.round：.5 一律向正無窮取（Python round 是 banker's，不同）。"""
    return int(math.floor(v + 0.5))


def num(v) -> str:
    """postmkt/index.html 的 num()：千分位整數，null→—。"""
    return "—" if v is None else f"{js_round(v):,}"


def jsnum(v) -> str:
    """模板字串直接內插數字時的 JS 行為：整數浮點不帶 .0。"""
    if v is None:
        return "null"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def fixed(v, nd: int) -> str:
    return f"{v:.{nd}f}"


def signed(v, nd: int) -> str:
    """(v>0?"+":"")+v.toFixed(nd)，null→—。"""
    return "—" if v is None else ("+" if v > 0 else "") + f"{v:.{nd}f}"


def yi_k(v) -> str:
    """千元→億：postmkt 的 yiK。"""
    return "—" if v is None else f"{v / 1e5:.1f}"


def yi_y(v) -> str:
    """元→億：postmkt 的 yiY。"""
    return "—" if v is None else f"{v / 1e8:.1f}"


def clean_sub_pm(s) -> str:
    """postmkt 的 cleanSub：去掉「xxx·」前綴。"""
    return re.sub(r"^.*?·", "", str(s or ""), count=1)


def clean_sub_v2(s) -> str:
    """v2 的 cleanSub：去掉尾端括號說明。"""
    return re.sub(r"\s*[（(].*$", "", str(s or ""))


def day10(s) -> str:
    return str(s or "")[:10]


def norm_date(s) -> str:
    return str(s or "").replace("/", "-")


def dates_json(dates: dict) -> str:
    """對齊 JS JSON.stringify（無空白）。"""
    return json.dumps(dates, ensure_ascii=False, separators=(",", ":"))


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI)


def slot_trading_day(slot: str) -> str:
    """該場次對應的目標交易日（台北 YYYY-MM-DD）。
    pm 場 cron 排 22:47 台北，但 GitHub Actions 常延遲 1~2h（實測延到隔日 00:0x 才
    啟動）；此時直接用 taipei_now() 會把 today 滾成「隔天」——閘門於是去硬等一個還沒
    開盤的新交易日的 postmkt/news/taiwan-flows，170 分全落空、永久 skip（缺 pm 存檔
    的真因）。故 pm 場在凌晨啟動（hour<12）時，把目標交易日回推一天＝觸發當晚的交易日；
    22:47 準點或小延遲（hour>=12）則就是當日。am 場 cron 06:23、不跨中午，直接用今日。"""
    now = taipei_now()
    if slot == "pm" and now.hour < 12:
        return (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def taipei_day_of(iso_str: str) -> str:
    """ISO 時間字串 → 台北時區日期 YYYY-MM-DD；解析失敗退回前 10 碼。"""
    s = str(iso_str or "")
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(TAIPEI).strftime("%Y-%m-%d")
    except ValueError:
        return day10(s)


def taipei_dt_of(iso_str: str):
    """ISO 時間字串 → 台北時區 datetime；解析失敗回 None（閘門判斷用）。"""
    s = str(iso_str or "")
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(TAIPEI)
    except ValueError:
        return None


def news_fresh(generated_at, today: str, min_hour: int, next_day_before=None) -> bool:
    """news.json 是否為「今日、且台北時刻 >= min_hour 點」的班次產出。
    新聞管線每天三班（06:30/15:00/22:37 台北）都會更新 generated_at，
    只看日期會被較早的班次誤滿足：
    - pm 場 min_hour=21：要的是 22:37 晚班全天新聞，不能被 15:00 午班放行；
    - am 場 min_hour=6 ：要的是 06:30 早班（清晨新聞餵晨報時段）。

    next_day_before：pm 晚班常因 GitHub Actions schedule 觸發延遲而跨過台北午夜
    才落地（實測 00:1x），此時 generated_at 台北日滾成隔日、時刻變 0，只認同日會
    永久誤擋（缺 pm 存檔的根因）。給定此值（pm 傳 5）時額外接受「隔日 00:00～
    next_day_before 點前」的晚班——該時窗內不會有別班次（次日早班 06:30 才跑），
    可安全視為前一交易日的晚班。am 場不傳（None），行為與原本完全一致。"""
    d = taipei_dt_of(generated_at)
    if d is None:
        return False
    ds = d.strftime("%Y-%m-%d")
    if ds == today and d.hour >= min_hour:
        return True
    if next_day_before is not None:
        tmr = (dt.datetime.strptime(today, "%Y-%m-%d") + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        if ds == tmr and d.hour < next_day_before:
            return True
    return False


def us_is_today(us, today: str) -> bool:
    """us.json 是否為今日產出。實查（2026-07-14）us.json 有兩個日期欄：
    date=美股交易日（美國休市/週末時不推進，拿來判「今日檔」會誤擋）、
    generated_at=產出時間（us.yml 每交易日台北清晨重跑必更新）——故用
    generated_at 的台北日判斷。"""
    return taipei_day_of((us or {}).get("generated_at")) == today


def http_json(url: str, timeout: int = 60, bust: bool = True):
    """GET JSON；bust=True 加 cache-buster（raw.githubusercontent 有 ~5 分快取，輪詢要繞過）。"""
    if bust:
        url = url + ("&" if "?" in url else "?") + f"t={int(time.time())}"
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def try_json(url: str, label: str):
    try:
        return http_json(url)
    except Exception as e:
        print(f"  {label} 載入失敗（該資料段將略過）：{e}", flush=True)
        return None


class DLabel:
    """dlabel 跨日警告機制（三站 JS 完全相同的邏輯）。"""

    def __init__(self, primary: str):
        self.primary = primary
        self.dates: dict[str, str] = {}

    def __call__(self, name: str, sec_date) -> str:
        self.dates[name] = sec_date or "?"
        if not sec_date:
            return f"【{name}（資料日不明）】"
        if norm_date(sec_date) == norm_date(self.primary):
            return f"【{name}（資料日 {sec_date}）】"
        return (f"【{name}（⚠資料日 {sec_date}，與主資料日 {self.primary} 不同，"
                f"請勿與其他段落跨日直接比較）】")


# ---------- 頁面1：盤後分析（postmkt/index.html insightGatherContext 移植） ----------

def aetf_advice(diff: dict) -> list[str]:
    """postmkt aetfAdvice() 的純文字版（JS gather 時會把 HTML 標籤剝掉，這裡直接產純文字）。"""
    out = []
    st = diff.get("stocks") or []
    sb = diff.get("subs") or []
    co = [s for s in st if sum(1 for v in (s.get("by") or {}).values() if v > 0) >= 2][:3]
    if co:
        out.append("多檔同買：" + "、".join(s.get("n") or s.get("c") or "" for s in co))
    cs = [s for s in st if sum(1 for v in (s.get("by") or {}).values() if v < 0) >= 2][:2]
    if cs:
        out.append("多檔同賣：" + "、".join(s.get("n") or s.get("c") or "" for s in cs))
    inn = next((s for s in sb if (s.get("val") or 0) > 0), None)
    outv = next((s for s in sb if (s.get("val") or 0) < 0), None)
    if inn:
        out.append(f"次產業最大加碼：{clean_sub_pm(inn.get('name'))} +{inn['val'] / 1e8:.1f}億")
    if outv:
        out.append(f"最大減碼：{clean_sub_pm(outv.get('name'))} {outv['val'] / 1e8:.1f}億")
    for k, v in (diff.get("etfs") or {}).items():
        ef = v.get("est_flow")
        if ef and abs(ef) >= 10e8:
            out.append(f"{k} 淨{'申購' if ef > 0 else '贖回'}約 {abs(ef) / 1e8:.0f}億")
    return out


def gather_postmkt(pm: dict, aetf_latest, aetf_diff, tf) -> dict:
    d = pm or {}
    a = {"latest": aetf_latest, "diff": aetf_diff}
    parts = []
    primary = d.get("date") or ""
    dlabel = DLabel(primary)

    # 主動ETF（跨repo，投信揭露日常與盤後籌碼不同日，特別要標）
    diff = a["diff"]
    if diff and (diff.get("stocks") or []):
        gd = diff.get("generated_dates") or []
        a_date = (gd[-1] if gd else "") or ((a["latest"] or {}).get("run_date")) or ""
        adv = aetf_advice(diff)
        st = "；".join(
            f"{s.get('c', '')}{s.get('n') or ''} 合計{'+' if (s.get('zh') or 0) > 0 else ''}{jsnum(s.get('zh'))}張"
            f" 金額{yi_y(s.get('val'))}億"
            for s in (diff.get("stocks") or [])[:12])
        subs = "；".join(f"{clean_sub_pm(s.get('name'))} {yi_y(s.get('val'))}億"
                         for s in (diff.get("subs") or [])[:6])
        parts.append(f"{dlabel('主動ETF 主動加減碼', a_date)}\n重點：{'；'.join(adv) or '—'}\n"
                     f"進出個股(前12)：{st}\n次產業流向(前6)：{subs}")

    # 三大法人買賣超（跨repo：盤後法人動態站 taiwan-flows；資料缺漏時整段略過）
    tf = tf or {}
    f_pg = (tf.get("pages") or {}).get("foreign")
    t_pg = (tf.get("pages") or {}).get("trust")
    if f_pg or t_pg:
        def tf_yi(v):
            return "—" if v is None else ("+" if v > 0 else "") + f"{v / 1e5:.1f}"

        def row(r):
            hold = "—" if r.get("hold_pct") is None else f"{r['hold_pct']:.1f}"
            return (f"{r.get('code', '')}{r.get('name') or ''} {tf_yi(r.get('net_amt_k'))}億/"
                    f"{num(r.get('net_lots'))}張 持股{hold}% 漲跌{signed(r.get('chg_pct'), 1)}%")

        seg = []
        if f_pg:
            seg.append("外資買超前10：" + ("；".join(row(r) for r in (f_pg.get("buy_by_amt") or [])[:10]) or "—"))
            seg.append("外資賣超前6：" + ("；".join(row(r) for r in (f_pg.get("sell_by_amt") or [])[:6]) or "—"))
            fc = f_pg.get("futures_card")
            if fc:
                vs = fc.get("vs_prev_month_lots")
                seg.append(f"外資台指期未平倉：淨{num(fc.get('oi_net_lots'))}口，"
                           f"較上月底({fc.get('prev_month_end') or '—'})"
                           f"{'+' if (vs or 0) > 0 else ''}{num(vs)}口")
        if t_pg:
            seg.append("投信買超前10：" + ("；".join(row(r) for r in (t_pg.get("buy_by_amt") or [])[:10]) or "—"))
            seg.append("投信賣超前6：" + ("；".join(row(r) for r in (t_pg.get("sell_by_amt") or [])[:6]) or "—"))
        if seg:
            parts.append(f"{dlabel('三大法人買賣超（盤後法人動態站）', tf.get('date'))}\n" + "\n".join(seg))

    # 融借券整合排行（依兩平台借券餘額；含放空/融資/當沖/三大法人）
    lr = _augment_lending((d.get("lending") or {}).get("rows") or [])
    if lr:
        def lend_line(r):
            usage = "—" if r.get("usage_ratio") is None else f"{r['usage_ratio']:.0f}"
            credit = "—" if r.get("credit_ratio") is None else f"{r['credit_ratio']:.1f}"
            return (f"{r.get('c', '')}{r.get('n') or ''}"
                    f"|借券餘額{num(r.get('plat_total'))}張(異動{'+' if (r.get('plat_total_chg') or 0) > 0 else ''}{jsnum(r.get('plat_total_chg'))})"
                    f"|借賣{num(r.get('sbl_short_bal'))}張(異動{'+' if (r.get('sbl_short_chg') or 0) > 0 else ''}{jsnum(r.get('sbl_short_chg'))},使用率{usage}%)"
                    f"|融資{num(r.get('margin_bal'))}張(異動{'+' if (r.get('margin_chg') or 0) > 0 else ''}{jsnum(r.get('margin_chg'))},券資比{credit}%)"
                    f"|外資{yi_k(r.get('foreign_net'))}億/投信{yi_k(r.get('trust_net'))}億/自營{yi_k(r.get('dealer_net'))}億"
                    f"|當沖{yi_k(r.get('dt_amt'))}億")

        parts.append(f"{dlabel('融借券整合排行 前25（依兩平台借券餘額）', (d.get('lending') or {}).get('date'))}\n"
                     + "\n".join(lend_line(r) for r in lr[:25]))

    # 當沖排行
    dtr = (d.get("daytrading") or {}).get("by_amount") or []
    if dtr:
        def dt_line(r):
            ratio = "—" if r.get("ratio") is None else f"{r['ratio']:.0f}"
            amp = "—" if r.get("amp_pct") is None else f"{r['amp_pct']:.1f}"
            traders = ",".join(f"{t.get('trader', '')}{num(t.get('vol'))}張"
                               for t in (r.get("traders") or [])[:3])
            return (f"{r.get('c', '')}{r.get('n') or ''}|當沖{yi_y(r.get('amt'))}億 量{num((r.get('vol') or 0) / 1000)}張"
                    f" 比重{ratio}%|漲跌{signed(r.get('chg_pct'), 1)}% 振幅{amp}%|分點推估:{traders}")

        parts.append(f"{dlabel('當沖排行 前15', (d.get('daytrading') or {}).get('date'))}\n"
                     + "\n".join(dt_line(r) for r in dtr[:15]))

    # 鉅額（依股票分組，取各股一筆代表＝總量/總額）
    bt = (d.get("blocktrade") or {}).get("rows") or []
    if bt:
        seen, groups = set(), []
        for r in bt:
            if r.get("c") not in seen:
                seen.add(r.get("c"))
                groups.append(r)
        parts.append(f"{dlabel('鉅額交易 依總金額前15', (d.get('blocktrade') or {}).get('date'))}\n"
                     + "；".join(f"{r.get('c', '')}{r.get('n') or ''} 總{num((r.get('stock_vol') or 0) / 1000)}張/"
                                 f"{yi_y(r.get('stock_money'))}億" for r in groups[:15]))

    # 零股盤中
    od = ((d.get("oddlot") or {}).get("intraday") or {}).get("rows") or []
    if od:
        parts.append(f"{dlabel('零股盤中 依金額前15', ((d.get('oddlot') or {}).get('intraday') or {}).get('date'))}\n"
                     + "；".join(f"{r.get('c', '')}{r.get('n') or ''} {(r.get('amt') or 0) / 1e4:.0f}萬元"
                                 f" {num(r.get('deals'))}筆" for r in od[:15]))

    return {"text": "\n\n".join(parts), "primary": primary, "dates": dlabel.dates}


def _augment_lending(rows: list) -> list:
    """postmkt.json 瘦身（2026-07-24）後 lending.rows 只存基礎量＋px，衍生欄由消費端重建。
    本函式只重建 gather_postmkt 消費的 5 欄（plat_total/plat_total_chg/*_net）；
    完整重建版在 index.html augmentLending()，公式一致（張×px＝千元），改動需同步。
    舊版資料（無 px 欄）原樣返回，前後版相容。"""
    if not rows or "px" not in rows[0]:
        return rows
    for r in rows:
        px = r.get("px")
        r["plat_total"] = (r.get("sys_bal") or 0) + (r.get("otc_bal") or 0)
        r["plat_total_chg"] = (r.get("sys_chg") or 0) + (r.get("otc_chg") or 0)
        for base, out in (("foreign_vol", "foreign_net"), ("trust_vol", "trust_net"),
                          ("dealer_vol", "dealer_net")):
            v = r.get(base)
            r[out] = js_round(v * px) if (px and v is not None) else None
    return rows


def fetch_brokers_context(pm: dict, primary: str) -> str:
    """postmkt insightFetchBrokers() 移植：指定分點當日進出（FinMind），
    依股票聚合成買賣超金額，各分點取前8買/賣。無 FINMIND_TOKEN 則整段標略過。"""
    if not fm_token():
        return "【指定分點單點進出】（未設定 FinMind token，略過分點分析）"
    brokers = (pm or {}).get("brokers") or {}
    date = brokers.get("date") or ""
    if date and primary and date != primary:
        dwarn = f"（⚠資料日 {date}，與主資料日 {primary} 不同，勿跨日比較）"
    else:
        dwarn = f"（資料日 {date or '?'}）"
    nm_by_id = {x.get("id"): x.get("name") for x in (brokers.get("list") or [])}
    # 股票代號→名稱：從 postmkt.json 現成資料建（同前端 buildBkNm）
    bk_nm: dict[str, str] = {}
    d = pm or {}
    for src in [((d.get("oddlot") or {}).get("intraday") or {}).get("rows"),
                ((d.get("oddlot") or {}).get("after") or {}).get("rows"),
                (d.get("lending") or {}).get("rows")]:
        for r in src or []:
            if r.get("c") and r["c"] not in bk_nm:
                bk_nm[r["c"]] = r.get("n") or ""
    out = []
    for bid in INSIGHT_BROKERS:
        try:
            # 專屬 endpoint（非 /api/v4/data），走 fmclient 統一重試；失敗吞掉＝該分點段落略過（原語意）
            try:
                raw = api_get("TaiwanStockTradingDailyReport", url=URL_FINMIND_TDR,
                              date=date, securities_trader_id=bid)
            except Exception:
                raw = []
            bname = (raw[0].get("securities_trader") if raw else None) or nm_by_id.get(bid) or bid
            agg: dict[str, float] = {}
            for x in raw:
                c = x.get("stock_id")
                agg[c] = agg.get(c, 0) + ((x.get("buy") or 0) - (x.get("sell") or 0)) * (x.get("price") or 0)
            rows = [{"c": c, "net": n} for c, n in agg.items()]
            buys = sorted((o for o in rows if o["net"] > 0), key=lambda o: -o["net"])[:8]
            sells = sorted((o for o in rows if o["net"] < 0), key=lambda o: o["net"])[:8]

            def fmt(o):
                return f"{o['c']}{bk_nm.get(o['c'], '')}{o['net'] / 1e4:.0f}萬"

            out.append(f"[{bid} {bname}] 買超前8：{','.join(fmt(o) for o in buys) or '—'}"
                       f"｜賣超前8：{','.join(fmt(o) for o in sells) or '—'}")
        except Exception as e:
            out.append(f"[{bid}] 查詢失敗：{e}")
    return f"【指定分點單點進出（買賣超金額，元）{dwarn}】\n" + "\n".join(out)


# ---------- 頁面2：即時類股動態（taiwan-flow-live-v2/index.html 移植） ----------

def gather_live(live, classify) -> dict:
    if not live:
        return {"text": "", "primary": "", "dates": {}}
    cl = classify or {}
    parts = []
    # 主資料日：ts 內含日期就用 ts，否則由 generated_at 換算台灣日期
    tsm = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", str(live.get("ts") or ""))
    primary = (tsm.group(0).replace("/", "-") if tsm
               else (taipei_day_of(live["generated_at"]) if live.get("generated_at") else ""))
    dlabel = DLabel(primary)

    def f1(v):
        return "—" if v is None else f"{v:.1f}"

    # a. 大盤：加權/櫃買指數 ＋ 漲跌家數/成交值
    ix = live.get("index") or {}
    mk = live.get("market") or {}

    def one(label, dd, m):
        li = []
        # chgP=漲跌點數、chg=漲跌%（worker idxOut 定義），兩者都給模型
        if dd and dd.get("val") is not None:
            li.append(f"{label}指數 {dd['val']:.2f}（{signed(dd.get('chgP'), 2)}點/{signed(dd.get('chg'), 2)}%）"
                      f"成交{f1(dd.get('amt_yi'))}億")
        if m:
            amt = f" 分類股總額{f1(m.get('amt_yi'))}億" if m.get("amt_yi") is not None else ""
            up = "—" if m.get("up") is None else jsnum(m["up"])
            down = "—" if m.get("down") is None else jsnum(m["down"])
            flat = "—" if m.get("flat") is None else jsnum(m["flat"])
            li.append(f"漲{up}(漲停{jsnum(m.get('up_lim') or 0)})/跌{down}(跌停{jsnum(m.get('down_lim') or 0)})"
                      f"/平{flat}{amt}")
        return "，".join(li)

    a_txt = one("加權", ix.get("tse"), mk.get("tse"))
    b_txt = one("櫃買", ix.get("otc"), mk.get("otc"))
    if a_txt or b_txt:
        parts.append(f"{dlabel('大盤即時', primary)}\n" + "\n".join(x for x in (a_txt, b_txt) if x))

    # b. 產業別資金：exchange 依 tse.amt_yi 前8強＋依平均漲跌跌勢前5
    ex = [{"sector": s.get("sector"), **(s.get("tse") or {})} for s in (live.get("exchange") or [])]
    ex = [r for r in ex if (r.get("n") or 0) > 0]
    if ex:
        def ex_line(r):
            return (f"{r.get('sector', '')} 成交{f1(r.get('amt_yi'))}億 平均{signed(r.get('avg_chg'), 2)}%"
                    f" 貢獻{signed(r.get('pts'), 2)}點 漲{jsnum(r.get('up'))}/跌{jsnum(r.get('down'))}({jsnum(r.get('n'))}檔)")

        top = sorted(ex, key=lambda r: -(r.get("amt_yi") or 0))[:8]
        weak = sorted((r for r in ex if (r.get("avg_chg") or 0) < 0),
                      key=lambda r: r.get("avg_chg") or 0)[:5]
        seg = ["資金前8強：\n" + "\n".join(ex_line(r) for r in top)]
        if weak:
            seg.append("跌勢前5：\n" + "\n".join(ex_line(r) for r in weak))
        parts.append(f"{dlabel('產業別資金（加權市場）', primary)}\n" + "\n".join(seg))

    # c. 資金湧入：flow.subs 依集中10分(c1) 前12；盤前 flow=null 明確註明
    fl = live.get("flow")
    if not fl or not (fl.get("subs") or []):
        parts.append(f"{dlabel('次產業盤中資金集中', primary)}\n盤前，無盤中資金集中資料。")
    else:
        subs = sorted(fl["subs"],
                      key=lambda s: -(s["c1"] if s.get("c1") is not None else (s.get("d_yi") or 0)))[:12]

        def sub_line(s):
            c1 = "—" if s.get("c1") is None else f"{s['c1']:.2f}"
            c2 = "—" if s.get("c2") is None else f"{s['c2']:.2f}"
            return (f"{clean_sub_v2(s.get('name'))} 近10分{f1(s.get('d_yi'))}億 集中10分{c1}×"
                    f" 集中30分{c2}× 窗內{signed(s.get('ret'), 2)}%")

        parts.append(f"{dlabel('次產業盤中資金集中（依集中10分倍數）', primary)}\n"
                     + "\n".join(sub_line(s) for s in subs))

    # d. 個股資金集中：sval 解碼，篩有盤中欄位(c10) 者，依 c10 前15；無盤中欄位則整段略過
    stocks = live.get("stocks") or {}
    cols = live.get("stock_cols") or SC

    def sval(code):
        arr = stocks.get(code)
        if not arr:
            return None
        return {k: (arr[i] if i < len(arr) else None) for i, k in enumerate(cols)}

    rows = []
    for c in stocks:
        v = sval(c)
        if v and v.get("c10") is not None:
            rows.append({"c": c, **v})
    rows.sort(key=lambda r: -(r["c10"] if r.get("c10") is not None else (r.get("c30") or 0)))
    rows = rows[:15]
    if rows:
        def stock_line(r):
            nm = (cl.get(r["c"]) or {}).get("n") or ""
            c10 = "—" if r.get("c10") is None else f"{r['c10']:.2f}"
            c30 = "—" if r.get("c30") is None else f"{r['c30']:.2f}"
            ints = "—" if r.get("ints") is None else signed(r["ints"], 2)
            vol = "—" if r.get("vol") is None else str(js_round(r["vol"]))
            return (f"{r['c']}{nm} 集中10分{c10}×/30分{c30}× 漲跌{signed(r.get('chg'), 2)}%"
                    f" 量{vol}張 投信連買{jsnum(r.get('it') or 0)}日/外資連買{jsnum(r.get('fi') or 0)}日 法人強度{ints}%")

        parts.append(f"{dlabel('個股盤中資金集中 前15（依集中10分倍數）', primary)}\n"
                     + "\n".join(stock_line(r) for r in rows))

    return {"text": "\n\n".join(parts), "primary": primary, "dates": dlabel.dates}


# ---------- 頁面3：新聞晨報（taiwan-stock-news/index.html 移植） ----------

TITLE_TAIL_RE = re.compile(r"^(.+?)\s+[-—–|]\s+([^-—–|]{1,20})$")
# JS 用 \p{L}\p{N}；Python 的 \w（UNICODE 預設）≒字母+數字+底線，另把底線併入移除
LOOSE_RE = re.compile(r"[\W_]+", re.UNICODE)


def loose_title_key(title) -> str:
    t = str(title or "").strip()
    m = TITLE_TAIL_RE.match(t)
    body = m.group(1).strip() if m else t
    return LOOSE_RE.sub("", body).lower()


def gather_news(data, morning, us) -> dict:
    d = data or {}
    tdays = sorted(d.get("trading_days") or [])
    primary = (tdays[-1] if tdays else "") or day10(d.get("generated_at") or "")
    dlabel = DLabel(primary)
    parts = []

    # a. 大盤與財金焦點新聞：全部 impact=market，去重（同文轉載）後依日期新→舊取前12
    mkt, seen = [], set()
    for s in d.get("stocks") or []:
        for n in s.get("news") or []:
            if n.get("impact") != "market":
                continue
            key = loose_title_key(n.get("title"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            mkt.append(n)
    mkt.sort(key=lambda n: str(n.get("date") or ""), reverse=True)
    if mkt:
        parts.append(f"{dlabel('大盤與財金焦點新聞 前12（新→舊）', primary)}\n"
                     + "\n".join(f"{day10(n.get('date'))}|{n.get('source', '')}|{n.get('title', '')}"
                                 for n in mkt[:12]))

    # b. 個股新聞熱度：依新聞則數前15，每檔附產業/權重/最新2則標題
    tops = sorted(d.get("stocks") or [],
                  key=lambda s: -(s.get("count") or len(s.get("news") or [])))[:15]
    if tops:
        def top_line(s):
            ns = "；".join(f"{day10(n.get('date'))}《{n.get('title', '')}》"
                           for n in sorted(s.get("news") or [],
                                           key=lambda n: str(n.get("date") or ""), reverse=True)[:2])
            w = f"{jsnum(s['weight_per'])}%" if s.get("weight_per") is not None else "—"
            return (f"{s.get('stock_id', '')}{s.get('name') or ''}{'[權值]' if s.get('heavyweight') else ''}"
                    f"|{s.get('industry') or '—'}|大盤權重{w}|新聞{jsnum(s.get('count') or len(s.get('news') or []))}則"
                    f"|最新：{ns or '—'}")

        parts.append(f"{dlabel('個股新聞熱度 前15（依新聞則數）', primary)}\n"
                     + "\n".join(top_line(s) for s in tops))

    # c. 晨報籌碼（跨repo讀v2；晨報資料日常與新聞主資料日不同，dlabel會標警告）
    if morning:
        m_date = day10(morning.get("generated_at") or "")
        c = morning.get("chips") or {}
        lines = []
        if morning.get("gap"):
            g, sp = morning["gap"], morning.get("spot") or {}
            close = jsnum(g["close"]) if g.get("close") is not None else "—"
            s_close = jsnum(sp["close"]) if sp.get("close") is not None else "—"
            lines.append(f"期指開盤參考：台指期夜盤{close}（{'+' if (g.get('chg_pct') or 0) > 0 else ''}{jsnum(g.get('chg_pct'))}%）"
                         f" vs 加權收盤{s_close}，價差{'+' if (g.get('gap') or 0) > 0 else ''}{jsnum(g.get('gap'))}點")
        if c.get("inst"):
            i = c["inst"]
            lines.append(f"三大法人買賣超（億）：外資{'+' if (i.get('foreign') or 0) > 0 else ''}{jsnum(i.get('foreign'))}"
                         f"｜投信{'+' if (i.get('trust') or 0) > 0 else ''}{jsnum(i.get('trust'))}"
                         f"｜自營{'+' if (i.get('dealer') or 0) > 0 else ''}{jsnum(i.get('dealer'))}")
        if c.get("it3"):
            lines.append("投信3日連買：" + "、".join((x.get("c") or "") + (x.get("n") or "") for x in c["it3"]))
        if c.get("it3_sell"):
            lines.append("投信3日連賣：" + "、".join((x.get("c") or "") + (x.get("n") or "") for x in c["it3_sell"]))
        if c.get("aetf"):
            lines.append("主動ETF動向：" + "；".join(c["aetf"]))
        # 晨報籌碼資料日：法人買賣超日優先，無則現貨收盤日，再無則產出日（generated_at 台北日）
        chip_date = ((c.get("inst") or {}).get("date")
                     or (morning.get("spot") or {}).get("date") or m_date)
        if lines:
            parts.append(f"{dlabel('晨報籌碼', chip_date)}\n" + "\n".join(lines))
        # d. 隔夜美股（美股本質為隔夜資料，資料日以 us.date=美股交易日為準，SYS已禁跨日串連）
        if us:
            u_lines = []
            if us.get("brief"):
                u_lines.append(us["brief"] + (f"（{us['session']}）" if us.get("session") else ""))
            for g in us.get("groups") or []:
                u_lines.append(f"{g.get('g', '')}：" + "、".join(
                    f"{r.get('n', '')}{signed(r['chg'], 2) + '%' if r.get('chg') is not None else '—'}"
                    for r in (g.get("rows") or [])[:3]))
            if u_lines:
                parts.append(f"{dlabel('隔夜美股', us.get('date'))}\n" + "\n".join(u_lines))
    else:
        parts.append("【晨報籌碼】（晨報資料未載入，本段略過，僅分析新聞面）")

    return {"text": "\n\n".join(parts), "primary": primary, "dates": dlabel.dates}


# ---------- SYS prompts（原樣取自三站前端與規格書，勿改寫） ----------
# SYS_POSTMKT 唯一事實來源＝本 repo index.html 的 SUM_SYS_POSTMKT 常數，
# 此處為移植複本——改動該常數時需逐字同步這裡（2026-07-24 已驗 953 字逐字一致）

SYS_POSTMKT = (
    "你是積極的台股盤後籌碼策略分析師。任務：彙整本頁各 tab 資料，產出以「找出 alpha 標的」為核心的洞見與可操作建議。"
    "要求："
    "(1) 進出建議一律以『同向共振』為基礎——多個資料源指向同一方向才算有效訊號（例：主動ETF加碼＋鉅額買盤同向＋外資買超同一檔＝法人共識偏多；或多源同步偏空）。"
    "(2) 只聚焦『同向共振』訊號；『背離』（多源方向不一致，如借券賣出大增但外資買超）不適合作為進出參考，本分析一律略過、無須呈現與描述——不要列出背離標的、不要有背離相關段落。"
    "(3) 依據資料呈現的歷史統計傾向做方向性判斷：對『同向共振』的標的可預測後續傾向、給偏多／偏空看法與明確進出建議。"
    "(4) 每一條洞見／建議都必須點名個股代號＋可追溯的具體數據依據，禁止空泛或無數據支撐的臆測。"
    "(5) 洞見與建議合計不超過 10 條，挑同向共振最強、依據最紮實的。"
    "(6) 建議個股以 alpha 為首要考量，優先納入中小型標的；大型權值股（如台灣50成分、2330台積電、2317鴻海、2454聯發科、金控股等）合計『不得超過』清單一半——這是上限而非目標，不必硬湊到接近一半，若中小型訊號更強就多放中小型、大型股少放甚至不放。"
    "(7) 推薦個股必須有『當日成交量 ≥ 1,000 張』的量能佐證，量能只能引自資料中有標示成交量的段落（如當沖排行的量、鉅額的總量、個股資金集中的量）；若該標的在所有帶量段落都查不到成交量，視為量能未知，一律不得列入推薦。"
    "(8) 指定分點進出僅供『輔助』參考、權重應調低：部分分點以隔日沖／當沖為主，當日大買未必代表中長期看多，容易誤導，故分點不可單獨作為進出依據，只在與其他資料源同向時才納入共振判斷。"
    "(9) 日期對齊：各段已標注資料日，若某段標注『與主資料日不同』，該段資料只能單獨解讀，嚴禁跨不同交易日直接比較或串連成訊號。"
    "(10) 繁體中文、markdown（## 標題、- 清單、**粗體**、可用表格）。"
    "結構：## 核心洞見與操作建議（≤10 條，每條格式＝個股代號名稱 ｜ 共振訊號 ｜ 數據依據 ｜ 方向與進出建議；大型股≤一半）／## 訊號邏輯（簡述同向共振推論）。不要有背離段落。"
    "最後一行附免責：以上為當日籌碼結構的模型即時研判、未經歷史回測、非保證獲利，僅供你自行參考。"
)

SYS_LIVE = (
    "你是積極的台股盤中即時資金流分析師。任務：彙整本頁即時類股資金動態，產出以「找出盤中 alpha 標的」為核心的洞見與可操作建議。"
    "要求：(1) 進出建議一律以『同向共振』為基礎——多個即時訊號指向同一方向才算有效（例：個股資金集中度飆升＋所屬產業資金同步湧入＋外資或投信連買＝資金共識偏多；多源同步偏空亦然）。"
    "(2) 只聚焦同向共振訊號；背離（多源方向不一致）不適合作為進出參考，一律略過、不呈現、不描述、不列背離段落。"
    "(3) 依即時資料呈現的資金傾向做方向性判斷，對共振標的給偏多／偏空看法與明確進出建議。"
    "(4) 每條洞見／建議必須點名個股代號＋可追溯的具體數據依據(集中倍數/貢獻點/連買日/漲跌%等)，禁止空泛臆測。"
    "(5) 洞見與建議合計不超過 10 條，挑共振最強、依據最紮實的。"
    "(6) 建議個股以 alpha 為首要考量，優先納入中小型標的；大型權值股(台灣50成分、2330台積電/2317鴻海/2454聯發科、金控股等)合計不得超過清單一半——這是上限而非目標，不必硬湊，中小型訊號更強就多放中小型甚至大型不放。"
    "(7) 個股成交量只出現在『個股盤中資金集中 前15』段的「量X張」，該段在沒有盤中資金集中資料時會整段略過、個別標的的量也可能是「—」，故個股評選不硬性套用『當日成交量 ≥ 1,000 張』門檻；量能查得到時仍應引用並優先採用達 1,000 張者，查不到者視為量能未知，仍可列入推薦但須於該檔標注『量能未知』。"
    "(8) 資料為盤中準即時快照(約分鐘級、20秒輪詢)，僅反映當下資金分布、非全日定論，判讀須註明時效性。"
    "(9) 繁體中文、markdown(## 標題、- 清單、**粗體**、可用表格)。"
    "結構：## 核心洞見與操作建議(≤10 條，每條＝個股代號名稱 ｜ 共振訊號 ｜ 數據依據 ｜ 方向與進出建議；大型股≤一半)／## 訊號邏輯(簡述同向共振推論)。不要有背離段落。"
    "最後一行附免責：以上為盤中即時資金結構的模型研判、資料為準即時快照、未經歷史回測、非投資建議、非保證獲利，僅供你自行參考。"
)

SYS_NEWS = (
    "你是積極的台股新聞與籌碼交叉分析師。任務：彙整本頁新聞焦點與晨報籌碼資料，產出以「找出具新聞催化＋籌碼支持的 alpha 標的」為核心的洞見與可操作建議。"
    "要求：(1) 進出建議一律以『同向共振』為基礎——新聞催化與籌碼方向一致才算有效訊號（例：個股利多新聞密集＋投信連買或主動ETF加碼＝催化與籌碼共振偏多；利空新聞＋法人賣超＝共振偏空）。"
    "(2) 只聚焦同向共振訊號；背離（如利多新聞但法人大賣）不適合作為進出參考，一律略過、不呈現、不描述、不列背離段落。"
    "(3) 依資料呈現的傾向做方向性判斷，對共振標的給偏多／偏空看法與明確進出建議。"
    "(4) 每條洞見／建議必須點名個股代號＋可追溯的具體依據(新聞標題與日期、法人買賣超數字、連買天數等)，禁止空泛臆測。"
    "(5) 洞見與建議合計不超過 10 條，挑共振最強、依據最紮實的。"
    "(6) 建議個股以 alpha 為首要考量，優先納入中小型標的；大型權值股(台灣50成分、2330台積電/2317鴻海/2454聯發科、金控股等)合計不得超過清單一半——上限而非目標，不必硬湊。"
    "(7) 新聞僅代表資訊面、非事實核實，判讀須註明消息面屬性；美股與晨報資料若與主資料日不同，可與其他資料串連解讀，但須標注各段自己的資料日、並註明該串連跨越不同交易日。"
    "(8) 繁體中文、markdown(## 標題、- 清單、**粗體**、可用表格)。"
    "結構：## 核心洞見與操作建議(≤10 條，每條＝個股代號名稱 ｜ 共振訊號 ｜ 依據 ｜ 方向與進出建議；大型股≤一半)／## 訊號邏輯(簡述共振推論)。"
    "不要有背離段落。最後一行附免責：以上為新聞與籌碼交叉的模型即時研判、未經歷史回測、非投資建議、非保證獲利，僅供你自行參考。"
)

SYS_SYNTH = (
    "你是台股首席策略師。以下是同一天由三個資料面向（盤後籌碼、即時資金流、新聞×晨報）"
    "各一次分析產出的 3 份獨立分析。任務：融會貫通、去蕪存菁，產出單一精華彙總。"
    "要求：(1)跨份共振優先——同一標的被多份分析同時點名且方向一致，即為最強 alpha 訊號，"
    "優先呈現並標注被幾份提及；僅單一份提及的標的降權或捨棄。"
    "(2)對精選標的明確給出：方向預測（偏多/偏空）、進出建議（進場條件/出場條件/停損思路）、投資建議與部位思路。"
    "(3)每檔標的附跨份依據摘要（哪幾份、什麼數據）。"
    "(4)大型權值股≤清單一半（上限非目標）。"
    "(5)精選標的若子分析引用了帶量數據，沿用『當日成交量 ≥ 1,000 張』門檻；來自即時類股動態或新聞晨報、本無個股級成交量佐證者不受此限，可納入選評，但須於依據摘要標注『量能未知』。"
    "(6)3 份分析若日期標注不同資料日，原則上嚴禁跨日串連；唯新聞晨報（含美股）的資料日不受此限，可與其他資料串連，但須標注其資料日。"
    "(7)結構：## 精華 alpha 標的（≤6 檔，每檔＝代號名稱｜共振強度(N/3份提及)｜方向預測｜進出建議｜依據摘要）"
    "／## 盤勢綜合研判（≤5行）。"
    "繁體中文 markdown。最後附免責：以上為多模型彙總之即時研判、未經歷史回測、非保證獲利，投資盈虧自負，僅供你自行參考。"
)


# ---------- Anthropic 呼叫 ----------

def anth_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k:
        raise RuntimeError("找不到環境變數 ANTHROPIC_API_KEY")
    return k


def _anth_headers() -> dict:
    return {"content-type": "application/json", "x-api-key": anth_key(),
            "anthropic-version": "2023-06-01"}


def msg_params(model: str, system: str, user_msg: str) -> dict:
    """單一請求參數（同步與 batch 兩路共用）：對齊三站前端 callClaude()——
    adaptive thinking + effort medium + max_tokens 16000。"""
    return {"model": model, "max_tokens": 16000,
            "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"},
            "system": system, "messages": [{"role": "user", "content": user_msg}]}


def parse_message(j: dict) -> dict:
    """message 物件 → {text, stop_reason, usage}（同步與 batch 兩路共用），異常一律丟例外。"""
    stop = j.get("stop_reason")
    if stop == "refusal":
        raise RuntimeError("模型基於安全政策婉拒本次請求")
    text = "\n".join(b.get("text", "") for b in (j.get("content") or []) if b.get("type") == "text")
    # adaptive thinking 吃滿 max_tokens 時只回 thinking block、沒有 text block，text 會靜靜落成
    # 空字串並以 ok:true 進彙總（2026-07-27 自動場實例）。當失敗丟出交給 retry／回退。
    if not text.strip():
        raise RuntimeError(f"回應無 text 內容（stop_reason={stop}），可能是 thinking 佔滿 max_tokens")
    return {"text": text, "stop_reason": stop, "usage": j.get("usage")}


def call_claude(model: str, system: str, user_msg: str) -> dict:
    r = requests.post(URL_ANTHROPIC, headers=_anth_headers(),
                      json=msg_params(model, system, user_msg), timeout=(15, 600))
    j = r.json()
    if not r.ok or j.get("type") == "error":
        raise RuntimeError((j.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return parse_message(j)


def call_claude_retry(model: str, system: str, user_msg: str, label: str) -> dict:
    """單份失敗 retry 1 次，仍失敗回 ok:false 佔位（不中止全場）。
    回傳一律帶 model＝這次實際送出的模型，讓呼叫端不必從常數回推（batch 超時回退到
    同步時，寫進產出的才會是真正被用到的那一個）。"""
    last = None
    for attempt in (1, 2):
        try:
            res = call_claude(model, system, user_msg)
            print(f"  ✓ {label}（第{attempt}次）", flush=True)
            return {"ok": True, "model": model, **res}
        except Exception as e:
            last = e
            print(f"  ✗ {label} 第{attempt}次失敗：{e}", flush=True)
            if attempt == 1:
                time.sleep(5)
    return {"ok": False, "model": model, "text": f"（該份產出失敗：{last}）",
            "stop_reason": None, "usage": None}


# ---------- Message Batches（2026-08-29：自動場改走半價非同步批次） ----------
# 官方規格：多數 batch 1 小時內完成、上限 24 小時、全部 token 半價。完成時間無保證，
# 故設每包期限：am 25 分（早上有時效，超時 cancel 改同步補做）、pm 180 分（無時效壓力，
# 但須留在 summary.yml timeout 240 分內，超時同樣回退——batch 尾端延遲不可讓全場死掉）。
# expired／errored／canceled 的個別請求同樣走同步回退。前端手動場不走 batch（互動要即時）。

URL_BATCHES = "https://api.anthropic.com/v1/messages/batches"
BATCH_POLL_SEC = 20
BATCH_DEADLINE_SEC = {"am": 25 * 60, "pm": 180 * 60}
# 全場時間預算：兩包 batch（摘要、彙總）各自的期限若都取滿，加上閘門硬等（pm 170 分/
# am 210 分）會超過 summary.yml timeout-minutes 240，job 被砍時連同步回退都來不及、
# 整場無產出。故每包期限＝min(場次期限, 全場剩餘預算−同步保留)；剩餘不足 60 秒直接
# 跳過 batch 走同步。t_start 從 main 進場（閘門之前）起算。
JOB_BUDGET_SEC = 225 * 60    # summary.yml timeout-minutes 240 留 15 分收尾（commit/push）
SYNC_RESERVE_SEC = 15 * 60   # 保留給同步回退（最多 4 次呼叫）的時間


def batch_deadline(slot: str, t_start: float) -> int:
    """本包 batch 可用秒數；回 0 表示剩餘預算不足、應跳過 batch 直接同步。"""
    remain = JOB_BUDGET_SEC - (time.monotonic() - t_start) - SYNC_RESERVE_SEC
    dl = int(min(BATCH_DEADLINE_SEC[slot], remain))
    return dl if dl >= 60 else 0


def call_claude_batch(reqs: dict, deadline_sec: int, label: str) -> dict:
    """提交一包請求並輪詢至 ended 或期限。reqs = {custom_id: (model, system, user_msg)}。
    回 {custom_id: {model,text,stop_reason,usage} 或 None}；None＝該筆需同步回退。
    整包提交失敗／超時 cancel／結果下載失敗 → 全部 None（整包回退），絕不讓場失敗。"""
    none_all = {cid: None for cid in reqs}
    try:
        r = requests.post(URL_BATCHES, headers=_anth_headers(),
                          json={"requests": [{"custom_id": cid,
                                              "params": msg_params(*p)} for cid, p in reqs.items()]},
                          timeout=(15, 120))
        j = r.json()
        if not r.ok or j.get("type") == "error":
            raise RuntimeError((j.get("error") or {}).get("message") or f"HTTP {r.status_code}")
        bid, status = j["id"], j.get("processing_status")
        print(f"  batch[{label}] 已提交 {bid}（{len(reqs)} 筆，期限 {deadline_sec // 60} 分）", flush=True)
    except Exception as e:
        print(f"  batch[{label}] 提交失敗（{e}）→ 全數同步回退", flush=True)
        return none_all
    t0 = time.monotonic()
    results_url = None
    while status != "ended":
        if time.monotonic() - t0 > deadline_sec:
            print(f"  batch[{label}] 超過期限仍未 ended（{status}）→ cancel 並全數同步回退", flush=True)
            try:
                requests.post(f"{URL_BATCHES}/{bid}/cancel", headers=_anth_headers(), timeout=(15, 60))
            except Exception:
                pass
            return none_all
        time.sleep(BATCH_POLL_SEC)
        try:
            jj = requests.get(f"{URL_BATCHES}/{bid}", headers=_anth_headers(), timeout=(15, 60)).json()
            status = jj.get("processing_status") or status
            results_url = jj.get("results_url") or results_url
        except Exception as e:
            print(f"  batch[{label}] 輪詢暫時失敗（{e}），續等", flush=True)
    try:
        rr = requests.get(results_url or f"{URL_BATCHES}/{bid}/results",
                          headers=_anth_headers(), timeout=(15, 300))
        rr.raise_for_status()
        out = dict(none_all)
        for line in rr.text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid, res = row.get("custom_id"), row.get("result") or {}
            if cid not in out:
                continue
            if res.get("type") == "succeeded":
                try:
                    # model 取這筆請求送出時用的模型（reqs[cid] 的第一元素），與同步回退
                    # 路徑同一語意，呼叫端拿到哪一路的結果都能直接記錄實際模型
                    out[cid] = {"model": reqs[cid][0], **parse_message(res.get("message") or {})}
                except Exception as e:
                    print(f"  batch[{label}] {cid} 內容異常（{e}）→ 該筆同步回退", flush=True)
            else:
                print(f"  batch[{label}] {cid} 結果 {res.get('type')} → 該筆同步回退", flush=True)
        took = int(time.monotonic() - t0)
        print(f"  batch[{label}] 完成（{took}s，{sum(1 for v in out.values() if v)}/{len(out)} 筆成功）", flush=True)
        return out
    except Exception as e:
        print(f"  batch[{label}] 結果下載失敗（{e}）→ 全數同步回退", flush=True)
        return none_all


# ---------- 資料齊全閘門 ----------

TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"


def is_twse_holiday(today: str) -> bool:
    """查 TWSE 官方休市行事曆（免金鑰）：today（YYYY-MM-DD）是否為排定休市日。
    - 行事曆混有「開始交易日/最後交易日」等交易日標記，須過濾：只有 Name 含「無交易」
      或 Description 含「放假/補假」的才是休市日（2026 全年 27 筆已逐筆驗證此規則）。
    - Date 為民國年 7 碼（1150101 = 2026-01-01）。
    - 只涵蓋排定假日；臨時颱風停市查不到（該殘餘風險已明確接受：僅 am 場可能誤跑，
      每次約 NT$9、每年 2-4 次；pm 場由 postmkt.json date 回退機制天然防住）。
    - API 失敗一律 fail-open 回 False（絕不因行事曆查不到而擋掉真交易日，
      後面還有資料齊全閘門把關）。"""
    try:
        y, m, d = today.split("-")
        roc = f"{int(y) - 1911}{m}{d}"
        rows = http_json(TWSE_HOLIDAY_URL)
        for r in rows:
            if str(r.get("Date", "")).strip() != roc:
                continue
            name = str(r.get("Name", ""))
            desc = str(r.get("Description", ""))
            if ("無交易" in name) or ("放假" in desc) or ("補假" in desc):
                return True
        return False
    except Exception as e:
        print(f"  休市行事曆查詢失敗（fail-open，續走資料閘門）：{e}", flush=True)
        return False


def wait_gate(slot: str, today: str) -> None:
    """資料齊全閘門（2026-07-14 依審計改造）。每 5 分輪詢一次。
    today＝該場次的目標交易日（slot_trading_day 算，pm 延遲跨午夜也釘在觸發當晚），
    不可用 taipei_now()——pm 被延到隔日凌晨啟動時會把 today 滾成隔天而永久 skip。
    pm：硬等三源皆為今日——postmkt.json date、news.json 晚班（台北日==今日且
        時刻>=21:00，避免被 15:00 午班滿足）、taiwan-flows latest.json date；
        最多 170 分（cron 22:47 台北起算，蓋住新聞晚班 22:37 起跑＋GitHub 延遲
        最壞 ~00:40 完成），逾時仍非今日 → print 原因後 exit 0（skip）。
    am：兩階段——
        第一階段硬等 morning.json generated_at 台北日==今日（最多 150 分，
        逾時 skip，沿用原邏輯）；
        第二階段軟等 us.json 為今日檔（generated_at 台北日，見 us_is_today）
        AND news.json 早班（台北日==今日且時刻>=06:00）；兩者皆備即放行，
        最多 60 分，逾時照跑（print 哪些源用舊檔，dlabel 機制會標資料日）。
    進場先查 TWSE 休市行事曆：排定假日直接 skip——am 場必須靠這層（晨報管線假日仍會
    更新 generated_at，資料閘門擋不住）；pm 場本可由資料閘門擋住，此層省去長時空轉。"""
    # 週末防護：cron 只排週一至五，但手動觸發（workflow_dispatch）在週末會空轉
    # 整段輪詢（盤後資料日永遠不會是週末），直接 skip。以「目標交易日」判週末，
    # pm 場延到週六凌晨啟動時目標日仍是週五交易日，不可誤判成週末。
    if dt.datetime.strptime(today, "%Y-%m-%d").weekday() >= 5:
        print(f"目標交易日 {today} 為週末非交易日，本場 skip。", flush=True)
        sys.exit(0)
    if is_twse_holiday(today):
        print(f"TWSE 休市行事曆：{today} 為排定休市日，本場 skip。", flush=True)
        sys.exit(0)

    if slot == "pm":
        max_min = 170
        deadline = time.time() + max_min * 60
        print(f"齊全閘門（pm）：等待 postmkt／news晚班／taiwan-flows 皆為今日 {today}，"
              f"最多 {max_min} 分鐘…", flush=True)
        while True:
            reasons = []
            try:
                pm = http_json(URL_POSTMKT)
                nw = http_json(URL_NEWS)
                tf = http_json(URL_TF)
                pm_ok = pm.get("date") == today
                n_ok = news_fresh(nw.get("generated_at"), today, 21, next_day_before=5)
                tf_ok = tf.get("date") == today
                if pm_ok and n_ok and tf_ok:
                    print(f"  postmkt.json date={pm.get('date')}、news.json 晚班"
                          f"（generated_at={nw.get('generated_at')}）、taiwan-flows "
                          f"date={tf.get('date')}，閘門通過", flush=True)
                    return
                if not pm_ok:
                    reasons.append(f"postmkt.json date={pm.get('date')}")
                if not n_ok:
                    reasons.append(f"news.json generated_at={nw.get('generated_at')}"
                                   f"（需今日台北 21:00 後、或隔日凌晨 05:00 前的晚班）")
                if not tf_ok:
                    reasons.append(f"taiwan-flows latest.json date={tf.get('date')}")
            except Exception as e:
                reasons.append(f"輪詢失敗：{e}")
            if time.time() >= deadline:
                print(f"閘門逾時（{max_min} 分）仍非今日資料，本場 skip：{'；'.join(reasons)}", flush=True)
                sys.exit(0)
            print(f"  尚未齊全（{'；'.join(reasons)}），5 分鐘後重試…", flush=True)
            time.sleep(300)

    # ---- am 場：第一階段硬等 morning.json（逾時 skip） ----
    max_min = 150
    deadline = time.time() + max_min * 60
    print(f"齊全閘門（am 第一階段）：等待 morning.json 為今日 {today}，最多 {max_min} 分鐘…", flush=True)
    while True:
        reasons = []
        try:
            m = http_json(URL_MORNING)
            m_day = taipei_day_of(m.get("generated_at"))
            if m_day == today:
                print(f"  morning.json 已是今日（{m_day}），第一階段通過", flush=True)
                break
            reasons.append(f"morning.json generated_at={m.get('generated_at')}（台北日 {m_day}）")
        except Exception as e:
            reasons.append(f"輪詢失敗：{e}")
        if time.time() >= deadline:
            print(f"閘門逾時（{max_min} 分）仍非今日資料，本場 skip：{'；'.join(reasons)}", flush=True)
            sys.exit(0)
        print(f"  尚未齊全（{'；'.join(reasons)}），5 分鐘後重試…", flush=True)
        time.sleep(300)

    # ---- am 場：第二階段軟等 us.json＋news 早班（逾時照跑，不 skip） ----
    soft_min = 60
    soft_deadline = time.time() + soft_min * 60
    print(f"齊全閘門（am 第二階段·軟等）：等待 us.json 今日檔＋news.json 早班（>=06:00），"
          f"最多 {soft_min} 分鐘，逾時照跑…", flush=True)
    while True:
        stale = []
        try:
            us = http_json(URL_US)
            nw = http_json(URL_NEWS)
            us_ok = us_is_today(us, today)
            n_ok = news_fresh(nw.get("generated_at"), today, 6)
            if us_ok and n_ok:
                print(f"  us.json generated_at={us.get('generated_at')}、"
                      f"news.json generated_at={nw.get('generated_at')} 皆今日，軟等通過", flush=True)
                return
            if not us_ok:
                stale.append(f"us.json generated_at={(us or {}).get('generated_at')}")
            if not n_ok:
                stale.append(f"news.json generated_at={nw.get('generated_at')}"
                             f"（需今日且台北 06:00 後的早班）")
        except Exception as e:
            stale.append(f"輪詢失敗：{e}")
        if time.time() >= soft_deadline:
            print(f"軟等逾時（{soft_min} 分），以下資料源將用舊檔照跑"
                  f"（dlabel 機制會標資料日）：{'；'.join(stale)}", flush=True)
            return
        print(f"  軟等中（{'；'.join(stale)}），5 分鐘後重試…", flush=True)
        time.sleep(300)


# ---------- 輸出與清理 ----------

def write_output(slot: str, trading_day: str, six: list[dict], synthesis: dict) -> None:
    now = taipei_now()  # 實際產生時刻（generated_at 用）；檔名與資料日改用 trading_day
    # 頂層 dates：三頁各自的主資料日（page→primary），由 six[] 彙整（同頁兩份 date 相同，後蓋前不影響）。
    # 供前端自動檔展開時顯示三頁資料日，與生成日（date/generated_at）區分。
    page_dates: dict[str, str] = {}
    for s in six:
        if s.get("page"):
            page_dates[s["page"]] = s.get("date") or ""
    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "slot": slot,
        "date": trading_day,
        "dates": page_dates,
        "six": six,
        "synthesis": synthesis,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{trading_day.replace('-', '')}-{slot}.json"
    dst = OUT_DIR / fname
    dst.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"輸出 {dst}（{dst.stat().st_size:,} bytes）", flush=True)

    # 刪超過 3 日的舊 am/pm 檔（只動自動場的 YYYYMMDD-{am|pm}.json，手動場不歸這裡管）
    cutoff = (now - dt.timedelta(days=3)).strftime("%Y%m%d")
    pat = re.compile(r"^(\d{8})-(am|pm)\.json$")
    for f in OUT_DIR.glob("*.json"):
        m = pat.match(f.name)
        if m and m.group(1) < cutoff:
            f.unlink()
            print(f"刪除舊檔 {f.name}", flush=True)

    # 刪 data/analyses/ 下超過 3 日的前端分析檔（三站摘要＋彙總手動；regex 嚴格比對，
    # 不動 data/summary/ 既有邏輯）
    ana_dir = ROOT / "data" / "analyses"
    if ana_dir.exists():
        ana_pat = re.compile(r"^(insight-(postmkt|live|news)|summary-manual)-(\d{8})\.json$")
        for f in ana_dir.glob("*.json"):
            m = ana_pat.match(f.name)
            if m and m.group(3) < cutoff:
                f.unlink()
                print(f"刪除舊分析檔 data/analyses/{f.name}", flush=True)

    # 重建 index.json（新→舊；pm 排在同日 am 前）
    runs = []
    for f in OUT_DIR.glob("*.json"):
        m = pat.match(f.name)
        if not m:
            continue
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        runs.append({"file": f.name, "date": j.get("date") or "",
                     "slot": m.group(2), "generated_at": j.get("generated_at") or ""})
    runs.sort(key=lambda r: (r["date"], r["slot"] == "pm", r["generated_at"]), reverse=True)
    idx = OUT_DIR / "index.json"
    idx.write_text(json.dumps({"runs": runs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"重建 {idx}（{len(runs)} 筆）", flush=True)


# ---------- 主流程 ----------

def load_sources() -> dict:
    """三個頁面的資料源全部載入；單源失敗不擋整場（該段自然略過，跟前端行為一致）。"""
    print("載入資料源…", flush=True)
    local_pm = ROOT / "data" / "postmkt.json"
    pm = None
    if local_pm.exists():  # Actions checkout 後本地讀更快，優先本地
        try:
            pm = json.loads(local_pm.read_text(encoding="utf-8"))
            print(f"  postmkt.json：本地檔（date={pm.get('date')}）", flush=True)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  本地 postmkt.json 讀取失敗，改抓遠端：{e}", flush=True)
    if pm is None:
        pm = try_json(URL_POSTMKT, "postmkt.json（遠端）")
    # 閘門確認過遠端是今日、但本地 checkout 可能是舊檔 → 換抓遠端
    today = taipei_now().strftime("%Y-%m-%d")
    if pm is not None and pm.get("date") != today:
        remote = try_json(URL_POSTMKT, "postmkt.json（遠端刷新）")
        if remote is not None and remote.get("date") == today:
            pm = remote
            print("  postmkt.json：本地為舊檔，已改用遠端今日檔", flush=True)
    classify = try_json(URL_CLASSIFY, "classify.json")
    return {
        "pm": pm,
        "aetf_latest": try_json(URL_AETF_LATEST, "aetf latest.json"),
        "aetf_diff": try_json(URL_AETF_DIFF, "aetf diff.json"),
        "tf": try_json(URL_TF, "taiwan-flows latest.json"),
        "live": try_json(URL_LIVE, "v2 /live"),
        "classify": (classify or {}).get("map") if classify else None,
        "news": try_json(URL_NEWS, "news.json"),
        "morning": try_json(URL_MORNING, "morning.json"),
        "us": try_json(URL_US, "us.json"),
    }


def build_contexts(src: dict) -> list[dict]:
    """回傳三個頁面各自的 {page, sys, user, primary}；user 格式與各站前端 USER 逐字對齊。"""
    pages = []

    # 頁面1：盤後分析
    g1 = gather_postmkt(src["pm"], src["aetf_latest"], src["aetf_diff"], src["tf"])
    bk_text = (fetch_brokers_context(src["pm"], g1["primary"]) if g1["text"].strip()
               else "【指定分點單點進出】（主資料未載入，略過）")
    user1 = (f"主資料日（以此為準）：{g1['primary']}\n各段資料日：{dates_json(g1['dates'])}\n"
             f"（凡標注「與主資料日不同」的段落，僅可單獨解讀，勿跨日比較）\n\n{g1['text']}\n\n{bk_text}")
    pages.append({"page": "盤後分析", "sys": SYS_POSTMKT, "user": user1,
                  "primary": g1["primary"], "empty": not g1["text"].strip()})

    # 頁面2：即時類股動態（自動場 08:00/22:00 都非盤中，盤中段天然單薄屬預期）
    g2 = gather_live(src["live"], src["classify"])
    user2 = (f"主資料日：{g2['primary']}\n各段資料日：{dates_json(g2['dates'])}\n"
             f"（凡標注與主資料日不同的段落僅可單獨解讀，勿跨日比較）\n\n{g2['text']}")
    pages.append({"page": "即時類股動態", "sys": SYS_LIVE, "user": user2,
                  "primary": g2["primary"], "empty": not g2["text"].strip()})

    # 頁面3：新聞晨報
    g3 = gather_news(src["news"], src["morning"], src["us"])
    user3 = (f"主資料日（以此為準）：{g3['primary']}\n各段資料日：{dates_json(g3['dates'])}\n"
             f"（凡標注「與主資料日不同」的段落，可與其他資料串連解讀，"
             f"但須標注該段資料日、並註明該串連跨越不同交易日）\n\n{g3['text']}")
    pages.append({"page": "新聞晨報", "sys": SYS_NEWS, "user": user3,
                  "primary": g3["primary"], "empty": not g3["text"].strip()})

    return pages


def main() -> None:
    ap = argparse.ArgumentParser(description="postmkt 彙總分析自動管線")
    ap.add_argument("--slot", choices=["am", "pm"], required=True,
                    help="場次：am=清晨場（cron 06:23 台北）/ pm=晚場（cron 22:47 台北）")
    ap.add_argument("--no-wait", action="store_true", help="跳過資料齊全輪詢閘門")
    ap.add_argument("--sync", action="store_true", help="不走 Batch、全程同步呼叫（除錯／緊急用）")
    ap.add_argument("--dry-run", action="store_true", help="只印三段 context，不呼叫 Anthropic（供驗收）")
    args = ap.parse_args()

    t_start = time.monotonic()   # 全場時間預算起點（batch_deadline 用，含閘門耗時）
    if not args.dry_run:
        anth_key()  # 缺 Secret 秒失敗（fail-fast），不等閘門輪詢完才發現

    trading_day = slot_trading_day(args.slot)
    # 已產出守門（2026-07-22，Worker 事件驅動化的備援去重）：本場當日檔已在 checkout 內
    # → 主場（Worker dispatch）已跑完，本 run 是 GH cron 備援排隊進來的 → 秒退，零 LLM 花費。
    # concurrency: build-summary 保證排隊不並跑，備援 run checkout 時必含主場 commit。
    done = OUT_DIR / f"{trading_day.replace('-', '')}-{args.slot}.json"
    if not args.dry_run and done.exists():
        print(f"{done.name} 已存在（主場已產出）→ 備援 run 跳過", flush=True)
        return
    if not args.no_wait and not args.dry_run:
        wait_gate(args.slot, trading_day)

    src = load_sources()
    pages = build_contexts(src)

    if args.dry_run:
        for p in pages:
            print(f"\n{'=' * 20} 【{p['page']}】context（主資料日 {p['primary'] or '—'}）{'=' * 20}")
            print(p["user"])
        print("\n--dry-run 完成（未呼叫 Anthropic）", flush=True)
        return

    if all(p["empty"] for p in pages):
        print("三個頁面 context 全部為空（資料源皆載入失敗），本場中止", flush=True)
        sys.exit(1)

    # 3 份摘要：3 context × Sonnet 5 各 1 次。主路徑走 Message Batches（半價非同步），
    # batch 超時／單筆失敗 → 該筆同步回退（call_claude_retry，retry 1 次後 ok:false 佔位）
    deadline = batch_deadline(args.slot, t_start)
    jobs = [(p, model, "Sonnet5") for p in pages for model in SUMMARY_MODELS]
    reqs = {f"s{i}": (model, p["sys"], p["user"])
            for i, (p, model, _tag) in enumerate(jobs) if not p["empty"]}
    print(f"呼叫 Anthropic：{len(reqs)} 份摘要"
          f"（{'同步模式' if args.sync or not deadline else f'Batch 半價，期限 {deadline // 60} 分'}）…",
          flush=True)
    bres = {} if args.sync or not reqs or not deadline else call_claude_batch(reqs, deadline, "摘要")

    # six[] 本來就存 model（前端 s.model 用來估費用）；batch/同步兩路的回傳現在也帶 model，
    # 展開後為同值覆蓋（reqs[f"s{i}"] 與 jobs[i] 同一個 model 變數），既有欄位值不變。
    six = []
    for i, (p, model, tag) in enumerate(jobs):
        label = f"{p['page']}×{tag}"
        if p["empty"]:
            six.append({"page": p["page"], "model": model, "tag": tag, "date": p["primary"],
                        "ok": False, "text": "（該份產出失敗：資料源載入失敗，context 為空）",
                        "stop_reason": None, "usage": None})
            continue
        got = bres.get(f"s{i}")
        if got:
            print(f"  ✓ {label}（batch）", flush=True)
            six.append({"page": p["page"], "model": model, "tag": tag, "date": p["primary"],
                        "ok": True, "via": "batch", **got})
        else:
            res = call_claude_retry(model, p["sys"], p["user"], f"{label}（同步{'' if args.sync else '回退'}）")
            six.append({"page": p["page"], "model": model, "tag": tag, "date": p["primary"],
                        "via": "sync", **res})

    ok_n = sum(1 for s in six if s["ok"])
    print(f"摘要完成：{ok_n}/{len(six)} 份成功", flush=True)
    if ok_n < MIN_OK_FOR_SYNTH:
        print(f"成功份數不足 {MIN_OK_FOR_SYNTH} 份，不做彙總，本場失敗", flush=True)
        sys.exit(1)

    # 彙總：3 份全文以【頁面×標籤】標頭分隔、附各份資料日；同樣 batch 優先、超時/失敗同步回退
    blocks = [f"【{s['page']}×{s.get('tag') or s['model']}】（資料日 {s['date'] or '—'}）\n{s['text']}" for s in six]
    synth_user = "\n\n".join(blocks)
    print(f"彙總中…（{SYNTH_MODEL}）", flush=True)
    synth = None
    dl2 = batch_deadline(args.slot, t_start)   # 重算剩餘預算（摘要 batch 可能耗掉大半）
    if not args.sync and dl2:
        got = call_claude_batch({"synth": (SYNTH_MODEL, SYS_SYNTH, synth_user)}, dl2, "彙總").get("synth")
        if got:
            synth = {"ok": True, "via": "batch", **got}
    if synth is None:
        synth = {"via": "sync", **call_claude_retry(SYNTH_MODEL, SYS_SYNTH, synth_user, f"彙總×{SYNTH_MODEL}")}
    if not synth["ok"]:
        print("彙總失敗，本場失敗", flush=True)
        sys.exit(1)

    # model 取自 synth 本身（batch 成功走 call_claude_batch、超時／失敗走 call_claude_retry，
    # 兩路都會回報自己實際用的模型），不寫 SYNTH_MODEL 字面量——前端才不必從程式碼推論
    write_output(args.slot, trading_day, six,
                 {"text": synth["text"], "usage": synth["usage"], "via": synth.get("via"),
                  "model": synth.get("model")})


if __name__ == "__main__":
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    main()
