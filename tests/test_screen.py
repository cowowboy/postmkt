# build_screen 解析/合併/排序純函式＋abort 不覆蓋舊檔。
# fixture 為 2026-08-12 實測 API 回應的縮樣（TradingView scanner／cnyes marketinfo），免網路免 token。
import json

import pytest

import build_screen as bs

# ---------- fixtures（實測回應縮樣） ----------

TV_RESP = {
    "totalCount": 4,
    "data": [
        # 欄位順序＝tv_payload 的 columns：name/description/close/feps/tp/rec
        {"s": "TWSE:6669", "d": ["6669", "Wiwynn Corporation", 5985, 359.050205, 7909.3, 1.05]},
        {"s": "TPEX:5274", "d": ["5274", "Aspeed Technology Inc", 5290, 245.5, 6100.0, 1.1]},
        {"s": "TWSE:2330", "d": ["2330", "TSMC", 2395, 140.53, 3221.117647, 1.097826]},
        {"s": "TWSE:2059", "d": ["2059", "King Slide Works Co., Ltd.", 11830, 330.596173, None, None]},
        {"s": "TWIDX:TAIEX", "d": ["TAIEX", "Taiwan Weighted", 24000, 999.0, None, None]},  # 非個股，應略過
        {"s": "TWSE:XXXX", "d": ["XXXX", "short row", 1.0]},  # 欄位不足，應略過
    ],
}

EST_2330 = [
    {"market": "TWS", "code": "2330", "financialYear": 2028, "rateDate": "2026-08-10",
     "feHigh": 210.6, "feLow": 149.77, "feMean": 177.94, "feMedian": 176.43, "numEst": 29},
    {"market": "TWS", "code": "2330", "financialYear": 2027, "rateDate": "2026-08-10",
     "feHigh": 159.67, "feLow": 124.6, "feMean": 140.53, "feMedian": 139.56, "numEst": 43},
    {"market": "TWS", "code": "2330", "financialYear": 2026, "rateDate": "2026-08-10",
     "feHigh": 113.19, "feLow": 98.4, "feMean": 107.48, "feMedian": 108.0, "numEst": 43},
]

TP_2330 = {"symbolId": "TWS:2330:STOCK", "chName": "台積電", "rateDate": "2026-08-08",
           "feHigh": 4200.0, "feLow": 2700.0, "feMean": 3221.12, "feMedian": 3175.0,
           "numEst": 34, "currency": "TWD", "last": 2395.0}

FACT_2330 = {"symbolId": "TWS:2330:STOCK", "chName": "台積電",
             "rateDate": [1785542400, 1785196800, 1782259200],  # 新到舊
             "feMark": [1.097826, 1.095745, 1.114583],
             "feBuy": [38, 39, 38], "feOver": [7, 7, 9], "feHold": [1, 1, 1],
             "feUnder": [0, 0, 0], "feSell": [0, 0, 0],
             "feMedian": [3175.0, 3160.0, 2750.0], "currency": "TWD"}


# ---------- TradingView 解析 ----------

def test_parse_tv_markets_and_columns():
    cands = bs.parse_tv(TV_RESP)
    assert [c["code"] for c in cands] == ["6669", "5274", "2330", "2059"]  # 非個股/短列已濾掉
    by = {c["code"]: c for c in cands}
    assert by["6669"]["mkt"] == "twse"
    assert by["5274"]["mkt"] == "tpex"
    assert by["2330"]["px"] == 2395
    assert by["2330"]["tv"] == {"feps": 140.53, "tp": 3221.12, "rec": 1.1}
    assert by["2059"]["tv"]["tp"] is None  # 無目標價欄保 null


def test_parse_tv_empty():
    assert bs.parse_tv({"data": []}) == []


# ---------- cnyes 解析 ----------

def test_parse_est_year_mapping():
    est = bs.parse_est(EST_2330)
    assert set(est) == {"2026", "2027", "2028"}
    assert est["2026"] == {"mean": 107.48, "median": 108.0, "high": 113.19, "low": 98.4,
                           "n": 43, "date": "2026-08-10"}
    assert est["2027"]["mean"] == 140.53


def test_parse_est_bad_data():
    assert bs.parse_est(None) is None
    assert bs.parse_est([]) is None
    assert bs.parse_est([{"noYear": 1}]) is None


def test_parse_tp():
    tp = bs.parse_tp(TP_2330)
    assert tp == {"median": 3175.0, "high": 4200.0, "low": 2700.0, "n": 34, "date": "2026-08-08"}
    assert bs.parse_tp(None) is None
    assert bs.parse_tp({"chName": "無估值", "feMedian": None}) is None


def test_parse_rating_latest_period():
    r = bs.parse_rating(FACT_2330)
    # 取 rateDate 最大那期（index 0）；鍵名對映 feBuy/feOver/feHold/feUnder/feSell
    assert r == {"buy": 38, "overweight": 7, "hold": 1, "underweight": 0, "sell": 0}


def test_parse_rating_unordered_dates():
    fact = dict(FACT_2330, rateDate=[1782259200, 1785542400])  # 舊到新也要取到最新
    fact["feBuy"] = [30, 38]
    assert bs.parse_rating(fact)["buy"] == 38


def test_parse_rating_bad_data():
    assert bs.parse_rating(None) is None
    assert bs.parse_rating({"rateDate": []}) is None
    assert bs.parse_rating({"rateDate": [1785542400]}) is None  # 全鍵缺 → None


# ---------- diag 合併 ----------

DIAG_STOCKS = {"2330": {"n": "台積電", "pe": 32.2, "yoy": 44.69, "mom": 5.62,
                        "rvs": 14, "f5": 12499, "t5": -707, "c": 2395.0}}


def test_diag_fields_present_and_absent():
    assert bs.diag_fields(DIAG_STOCKS, "2330") == {
        "pe": 32.2, "yoy": 44.69, "mom": 5.62, "rvs": 14, "f5": 12499, "t5": -707}
    # 1200 檔宇宙外：全鍵留 null
    assert bs.diag_fields(DIAG_STOCKS, "9999") == {
        "pe": None, "yoy": None, "mom": None, "rvs": None, "f5": None, "t5": None}


# ---------- 排序 ----------

def _row(feps=None, est=None):
    return {"tv": {"feps": feps, "tp": None, "rec": None}, "est": est}


def test_sort_key_prefers_next_year_mean():
    row = _row(feps=1.0, est={"2026": {"mean": 100.0}, "2027": {"mean": 140.0}})
    assert bs.sort_key(row, "2027", "2026") == 140.0


def test_sort_key_fallbacks():
    assert bs.sort_key(_row(feps=1.0, est={"2026": {"mean": 100.0}}), "2027", "2026") == 100.0
    assert bs.sort_key(_row(feps=55.0, est=None), "2027", "2026") == 55.0
    assert bs.sort_key(_row(), "2027", "2026") == float("-inf")  # 全缺沉底


# ---------- abort 不覆蓋舊檔 ----------

def test_abort_keeps_existing_output(tmp_path, monkeypatch):
    out = tmp_path / "screen.json"
    old = '{"date":"2026-08-11","n":1,"rows":[]}'
    out.write_text(old, encoding="utf-8")

    def boom(min_feps):
        raise bs.ScreenAbort("模擬初篩失敗")

    monkeypatch.setattr(bs, "fetch_tv", boom)
    assert bs.main(["--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == old  # 舊檔原封不動


def test_consecutive_cnyes_failures_abort(monkeypatch):
    monkeypatch.setattr(bs, "SLEEP_SEC", 0)
    monkeypatch.setattr(bs, "fetch_tv", lambda mf: [
        {"code": f"{1000 + i}", "name": str(i), "desc": None, "mkt": "twse", "px": 1.0,
         "tv": {"feps": 30.0, "tp": None, "rec": None}} for i in range(12)])
    monkeypatch.setattr(bs, "load_diag_stocks", lambda: {})
    monkeypatch.setattr(bs, "fetch_cnyes",
                        lambda code: ({"est": None, "tp": None, "rating": None, "ch_name": None}, True))
    with pytest.raises(bs.ScreenAbort):
        bs.build(20, None)


# ---------- 端到端（mock 網路）：組列＋排序＋寫檔 ----------

def test_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "SLEEP_SEC", 0)
    monkeypatch.setattr(bs, "fetch_tv", lambda mf: bs.parse_tv(TV_RESP))
    monkeypatch.setattr(bs, "load_diag_stocks", lambda: DIAG_STOCKS)

    def fake_fetch(code):
        if code == "2330":
            return ({"est": bs.parse_est(EST_2330), "tp": bs.parse_tp(TP_2330),
                     "rating": bs.parse_rating(FACT_2330), "ch_name": "台積電"}, False)
        # 其他檔：est 只有超高明年度 mean，驗排序用
        mean = {"6669": 400.0, "5274": 300.0, "2059": 380.0}[code]
        return ({"est": {"2027": {"mean": mean, "median": mean, "high": mean, "low": mean,
                                  "n": 5, "date": "2026-08-10"}},
                 "tp": None, "rating": None, "ch_name": None}, False)

    monkeypatch.setattr(bs, "fetch_cnyes", fake_fetch)

    class D:
        year = 2026

        @staticmethod
        def isoformat():
            return "2026-08-12"

    monkeypatch.setattr(bs, "taipei_today", lambda: D)
    payload = bs.build(20, None)
    assert payload["n"] == 4
    assert [r["code"] for r in payload["rows"]] == ["6669", "2059", "5274", "2330"]  # 2027 mean 降冪
    r2330 = payload["rows"][-1]
    assert r2330["name"] == "台積電"
    assert r2330["diag"]["pe"] == 32.2
    assert r2330["est"]["2026"]["mean"] == 107.48

    monkeypatch.setattr(bs, "build", lambda mf, lim: payload)
    out = tmp_path / "screen.json"
    assert bs.main(["--out", str(out)]) == 0
    j = json.loads(out.read_text(encoding="utf-8"))
    assert j["min_feps"] == 20 and j["n"] == 4
