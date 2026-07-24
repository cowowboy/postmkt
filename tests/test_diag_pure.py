# build_diag 純函式：streak（連買賣天數）、rev_metrics（營收 YoY/連續月數）、
# _pctile（估值百分位）、_roc_date（民國日期）——出錯會靜默污染 diag.json 燈號。
import build_diag as bd


# ---------- streak ----------

def test_streak_buy_run():
    assert bd.streak([1, -2, 3, 4, 5]) == 3


def test_streak_sell_run():
    assert bd.streak([2, -1, -3]) == -2


def test_streak_latest_zero_or_none_is_zero():
    assert bd.streak([5, 3, 0]) == 0
    assert bd.streak([5, 3, None]) == 0
    assert bd.streak([]) == 0


def test_streak_none_breaks_run():
    assert bd.streak([1, None, 2, 3]) == 2


# ---------- _pctile ----------

def test_pctile_needs_60_samples():
    assert bd._pctile([1.0] * 59, 1.0) is None
    assert bd._pctile([float(i) for i in range(1, 101)], 100.0) == 100.0
    assert bd._pctile([float(i) for i in range(1, 101)], 50.0) == 50.0


def test_pctile_ignores_zero_and_none():
    series = [None, 0] + [float(i) for i in range(1, 101)]
    assert bd._pctile(series, 100.0) == 100.0  # 去 0/None 後樣本數不變質


def test_pctile_no_current_value():
    assert bd._pctile([float(i) for i in range(100)], None) is None


# ---------- _roc_date ----------

def test_roc_date_slash_and_compact():
    assert bd._roc_date("115/07/15") == "2026-07-15"
    assert bd._roc_date("1150715") == "2026-07-15"
    assert bd._roc_date("115／07／15") == "2026-07-15"  # 全形斜線


def test_roc_date_invalid():
    assert bd._roc_date("") is None
    assert bd._roc_date("2026-07-15") is None
    assert bd._roc_date("11507") is None


# ---------- rev_metrics ----------

def _months(n, start_y=2024, start_m=1):
    out = []
    y, m = start_y, start_m
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def test_rev_metrics_yoy_mom():
    months = _months(14)  # 2024-01 .. 2025-02
    arr = [100.0] * 12 + [110.0, 121.0]  # 2025-01 YoY +10%、2025-02 YoY +21%
    r = bd.rev_metrics(months, arr)
    assert r["ym"] == "2025-02"
    assert round(r["yoy"], 1) == 21.0
    assert round(r["mom"], 1) == 10.0
    assert r["rvs"] == 2  # 連 2 個月 YoY 成長


def test_rev_metrics_decline_streak_negative():
    months = _months(14)
    arr = [100.0] * 12 + [90.0, 80.0]
    r = bd.rev_metrics(months, arr)
    assert r["rvs"] == -2


def test_rev_metrics_latest_none_falls_back():
    months = _months(13)
    arr = [100.0] * 12 + [None]  # 最新月缺值 → 用前一個有值月
    r = bd.rev_metrics(months, arr)
    assert r["ym"] == months[11]


def test_rev_metrics_empty():
    assert bd.rev_metrics([], []) is None
    assert bd.rev_metrics(_months(3), [None, None, None]) is None


# ---------- _next_exdiv（同日現金+股票合併需與輸入順序無關，2026-07-24 改寫） ----------

def test_next_exdiv_basic():
    rows = [{"CashExDividendTradingDate": "2026-08-01"}]
    assert bd._next_exdiv(rows, "2026-07-24") == ("2026-08-01", "現金")


def test_next_exdiv_past_dates_ignored():
    rows = [{"CashExDividendTradingDate": "2026-07-01"}]
    assert bd._next_exdiv(rows, "2026-07-24") == (None, None)


def test_next_exdiv_same_day_merge_any_order():
    import itertools
    base = [{"CashExDividendTradingDate": "2026-08-01"},
            {"StockExDividendTradingDate": "2026-08-01"},
            {"CashExDividendTradingDate": "2026-09-01"}]
    for perm in itertools.permutations(base):
        assert bd._next_exdiv(list(perm), "2026-07-24") == ("2026-08-01", "現金+股票"), perm


def test_next_exdiv_picks_earliest():
    rows = [{"StockExDividendTradingDate": "2026-09-01"},
            {"CashExDividendTradingDate": "2026-08-15"}]
    assert bd._next_exdiv(rows, "2026-07-24") == ("2026-08-15", "現金")
