# build_summary 日期閘門與純函式：news_fresh 跨午夜、slot_trading_day 延遲跨日、
# is_twse_holiday 民國年/過濾規則/fail-open、js_round、loose_title_key——
# 全是實際踩過坑（pm 場曾天天 skip）或出錯會靜默污染資料的邏輯，離線可跑。
import datetime as dt

import build_summary as bs


# ---------- js_round（JS Math.round 對齊；Python round 是 banker's） ----------

def test_js_round_half_away_from_banker():
    assert bs.js_round(2.5) == 3      # Python round(2.5)==2，JS 是 3
    assert bs.js_round(3.5) == 4
    assert bs.js_round(-0.5) == 0     # JS Math.round(-0.5) === -0 → 0
    assert bs.js_round(-1.5) == -1    # JS 向正無窮取
    assert bs.js_round(2.4) == 2


# ---------- slot_trading_day（pm 場延遲跨午夜要釘回觸發當晚的交易日） ----------

def _fix_now(monkeypatch, y, m, d, hh, mm=0):
    monkeypatch.setattr(bs, "taipei_now",
                        lambda: dt.datetime(y, m, d, hh, mm, tzinfo=bs.TAIPEI))


def test_slot_pm_on_time(monkeypatch):
    _fix_now(monkeypatch, 2026, 7, 24, 22, 47)
    assert bs.slot_trading_day("pm") == "2026-07-24"


def test_slot_pm_delayed_past_midnight_pins_previous_day(monkeypatch):
    # GitHub cron 延遲到隔日 00:0x 啟動：目標交易日必須回推到觸發當晚（缺 pm 存檔的真因）
    _fix_now(monkeypatch, 2026, 7, 25, 0, 12)
    assert bs.slot_trading_day("pm") == "2026-07-24"


def test_slot_am_never_shifts(monkeypatch):
    _fix_now(monkeypatch, 2026, 7, 24, 6, 23)
    assert bs.slot_trading_day("am") == "2026-07-24"
    _fix_now(monkeypatch, 2026, 7, 24, 0, 30)   # am 手動觸發在凌晨也不回推
    assert bs.slot_trading_day("am") == "2026-07-24"


# ---------- news_fresh（晚班跨午夜落地的接受窗） ----------

def test_news_fresh_same_day_after_min_hour():
    assert bs.news_fresh("2026-07-24T22:37:00+08:00", "2026-07-24", 21)
    assert not bs.news_fresh("2026-07-24T15:00:00+08:00", "2026-07-24", 21)  # 午班不得放行 pm


def test_news_fresh_cross_midnight_window():
    # 晚班延遲到隔日 00:1x：pm 傳 next_day_before=5 要接受；不傳（am 行為）要拒絕
    late = "2026-07-25T00:15:00+08:00"
    assert bs.news_fresh(late, "2026-07-24", 21, next_day_before=5)
    assert not bs.news_fresh(late, "2026-07-24", 21)
    # 隔日 05:00 以後已可能是別班次，不得再視為前一日晚班
    assert not bs.news_fresh("2026-07-25T05:01:00+08:00", "2026-07-24", 21, next_day_before=5)


def test_news_fresh_utc_input_converted_to_taipei():
    # generated_at 若是 UTC（Z 結尾），14:37Z＝台北 22:37 → 今日晚班
    assert bs.news_fresh("2026-07-24T14:37:00Z", "2026-07-24", 21)


def test_news_fresh_garbage_input():
    assert not bs.news_fresh("not-a-date", "2026-07-24", 21)
    assert not bs.news_fresh(None, "2026-07-24", 21)


# ---------- taipei_day_of ----------

def test_taipei_day_of():
    assert bs.taipei_day_of("2026-07-24T16:30:00Z") == "2026-07-25"   # UTC 跨日
    assert bs.taipei_day_of("2026-07-24T10:00:00+08:00") == "2026-07-24"
    assert bs.taipei_day_of("2026-07-24") == "2026-07-24"             # 無時刻→naive→視為 UTC 00:00→台北同日
    assert bs.taipei_day_of("garbage-in") == "garbage-in"[:10]        # 解析失敗退前 10 碼


# ---------- is_twse_holiday（民國年轉換＋交易日標記過濾＋fail-open） ----------

def _cal(monkeypatch, rows):
    monkeypatch.setattr(bs, "http_json", lambda url: rows)


def test_holiday_matched_by_roc_date(monkeypatch):
    _cal(monkeypatch, [{"Date": "1150724", "Name": "颱風假（無交易）", "Description": ""}])
    assert bs.is_twse_holiday("2026-07-24") is True


def test_trading_day_markers_filtered(monkeypatch):
    # 行事曆混有「開始交易日」等非休市標記，不得誤判為假日
    _cal(monkeypatch, [{"Date": "1150724", "Name": "開始交易日", "Description": "新春開始交易"}])
    assert bs.is_twse_holiday("2026-07-24") is False


def test_holiday_by_description(monkeypatch):
    _cal(monkeypatch, [{"Date": "1150101", "Name": "元旦", "Description": "依規定放假"}])
    assert bs.is_twse_holiday("2026-01-01") is True
    assert bs.is_twse_holiday("2026-01-02") is False  # 不同日不匹配


def test_holiday_api_failure_fail_open(monkeypatch):
    def boom(url):
        raise OSError("api down")
    monkeypatch.setattr(bs, "http_json", boom)
    assert bs.is_twse_holiday("2026-07-24") is False  # 絕不因行事曆掛掉擋真交易日


# ---------- loose_title_key（新聞標題去重鍵） ----------

def test_loose_title_key_strips_punct_and_case():
    assert bs.loose_title_key("台積電 Q2 財報！亮眼") == bs.loose_title_key("台積電Q2財報亮眼")
    assert bs.loose_title_key("ABC-123") == "abc123"
    assert bs.loose_title_key(None) == ""


# ---------- _augment_lending（postmkt.json 瘦身後的衍生欄重建） ----------

def test_augment_lending_reconstruction():
    rows = [{"px": 25.5, "sys_bal": 100, "otc_bal": 50, "sys_chg": 10, "otc_chg": -4,
             "foreign_vol": 30, "trust_vol": -7, "dealer_vol": 0}]
    out = bs._augment_lending(rows)
    assert out[0]["plat_total"] == 150
    assert out[0]["plat_total_chg"] == 6
    assert out[0]["foreign_net"] == bs.js_round(30 * 25.5)
    assert out[0]["trust_net"] == bs.js_round(-7 * 25.5)
    assert out[0]["dealer_net"] == 0  # 0 張是有效值 → 0；None 只在 px 缺或 vol 為 None


def test_augment_lending_zero_vol_gives_zero():
    rows = [{"px": 10.0, "sys_bal": 0, "otc_bal": 0, "sys_chg": 0, "otc_chg": 0,
             "foreign_vol": 0, "trust_vol": None, "dealer_vol": 5}]
    out = bs._augment_lending(rows)
    assert out[0]["foreign_net"] == 0
    assert out[0]["trust_net"] is None
    assert out[0]["dealer_net"] == 50


def test_augment_lending_no_px_field_passthrough():
    # 舊版資料（衍生欄已在、無 px）不得被改寫
    rows = [{"plat_total": 999, "foreign_net": 123}]
    out = bs._augment_lending(rows)
    assert out[0]["plat_total"] == 999 and out[0]["foreign_net"] == 123


def test_augment_lending_null_px():
    rows = [{"px": None, "sys_bal": 1, "otc_bal": 2, "sys_chg": 0, "otc_chg": 0,
             "foreign_vol": 10, "trust_vol": 1, "dealer_vol": 1}]
    out = bs._augment_lending(rows)
    assert out[0]["plat_total"] == 3          # 餘額合計不需 px
    assert out[0]["foreign_net"] is None      # 金額欄無價可估
