# build_postmkt 聚合函式（離線 fixture）：零股合計列過濾、lending 瘦身後欄位形狀、
# 當沖 by_ratio 停產。date 傳空字串可跳過所有網路呼叫（TWSE/分點推估）。
import build_postmkt as bp


# ---------- build_oddlot：TWSE 合計列過濾（TWTC7U/TWT53U 已知坑） ----------

def test_oddlot_filters_total_rows_keeps_etf():
    rows = [
        ["2330", "台積電", "1,000", "10", "500,000"],
        ["00679B", "元大美債20年", "2,000", "5", "60,000"],   # ETF 代號帶字母要保留
        ["合計", "", "999,999", "999", "9,999,999"],           # 中文合計列要濾掉
        ["", "小計", "888", "8", "88,888"],                    # 空代號列要濾掉
        ["2317", "鴻海", "0", "0", "0"],                       # 成交股數 0 要濾掉
    ]
    out = bp.build_oddlot("2026-07-24", rows, "", [])
    codes = [r["c"] for r in out["intraday"]["rows"]]
    assert codes == ["2330", "00679B"]  # 依金額排序
    assert out["intraday"]["rows"][0]["sh"] == 1000
    assert out["after"]["rows"] == []


# ---------- build_lending：瘦身後的欄位形狀（date="" 跳過 TWSE 網路呼叫） ----------

def _lending_fixture():
    margin_rows = [{"stock_id": "2330", "MarginPurchaseTodayBalance": 1000,
                    "MarginPurchaseYesterdayBalance": 900, "MarginPurchaseBuy": 50,
                    "MarginPurchaseLimit": 2000, "OffsetLoanAndShort": 3}]
    short_rows = [{"stock_id": "2330",
                   "SBLShortSalesCurrentDayBalance": 500_000,      # 股 → 500 張
                   "SBLShortSalesPreviousDayBalance": 400_000,
                   "MarginShortSalesCurrentDayBalance": 200_000,
                   "MarginShortSalesPreviousDayBalance": 250_000,
                   "MarginShortSalesQuota": 1_000_000,
                   "SBLShortSalesShortSales": 10_000, "SBLShortSalesReturns": 5_000}]
    inst_rows = [{"stock_id": "2330", "name": "Foreign_Investor", "buy": 3_000_000, "sell": 1_000_000},
                 {"stock_id": "2330", "name": "Investment_Trust", "buy": 100_000, "sell": 400_000}]
    price_rows = [{"stock_id": "2330", "close": 1000.0}]
    return bp.build_lending("", [], margin_rows, short_rows, [], "", price_rows,
                            inst_rows, [], {"2330": "台積電"})


def test_lending_slim_no_derived_fields():
    row = _lending_fixture()["rows"][0]
    # 衍生欄 2026-07-24 起不落地（由前端 augmentLending / build_summary._augment_lending 重建）
    for k in ("plat_total", "plat_total_chg", "plat_total_mv", "sys_mv_chg", "otc_mv_chg",
              "sbl_short_mv", "sbl_short_mv_chg", "margin_short_mv", "margin_short_mv_chg",
              "short_total", "short_total_chg", "short_total_mv", "short_total_mv_chg",
              "margin_mv", "margin_mv_chg", "foreign_net", "trust_net", "dealer_net",
              "foreign_shares_mv"):
        assert k not in row, f"{k} 應已從落地欄位移除"


def test_lending_slim_base_fields_and_px():
    row = _lending_fixture()["rows"][0]
    assert row["px"] == 1000.0
    assert row["sbl_short_bal"] == 500 and row["sbl_short_chg"] == 100
    assert row["margin_short_bal"] == 200 and row["margin_short_chg"] == -50
    assert row["margin_bal"] == 1000 and row["margin_chg"] == 100
    assert row["foreign_vol"] == 2000 and row["trust_vol"] == -300
    assert row["credit_ratio"] == 20.0   # 200/1000
    assert row["short_usage"] == 20.0    # 200/1000 張配額
    assert row["margin_usage"] == 50.0


def test_lending_augment_parity_with_old_backend_formula():
    """前端/摘要端重建公式應與舊後端公式等值（同一 fixture 手算對照）。"""
    import build_summary as bs
    row = dict(_lending_fixture()["rows"][0])
    out = bs._augment_lending([row])[0]
    px = 1000.0
    assert out["foreign_net"] == round(2_000_000 * px / 1000)  # 舊後端：股數差×px÷1000
    assert out["trust_net"] == round(-300_000 * px / 1000)
    assert out["plat_total"] == row["sys_bal"] + row["otc_bal"]


# ---------- build_daytrading：by_ratio 停產、聚合正確 ----------

def test_daytrading_no_by_ratio_and_metrics():
    rows = [{"stock_id": "2330", "Volume": 1_000_000, "BuyAmount": 2_000_000, "SellAmount": 4_000_000}]
    price_rows = [{"stock_id": "2330", "close": 102.0, "spread": 2.0, "max": 103.0, "min": 99.0,
                   "Trading_Volume": 2_000_000}]
    out = bp.build_daytrading("", rows, price_rows, {"2330": "台積電"})
    assert "by_ratio" not in out
    r = out["by_amount"][0]
    assert r["amt"] == 3_000_000          # (買+賣)/2
    assert r["ratio"] == 50.0             # 100萬/200萬股
    assert r["chg_pct"] == 2.0            # spread 2 / 前收 100
    assert r["amp_pct"] == 4.0            # (103-99)/100
    assert "traders" not in r             # date="" 跳過分點推估
