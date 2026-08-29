# call_claude_batch（Message Batches 主路徑＋同步回退訊號）離線測試：mock requests，
# 免 token 免網路。契約＝回 {custom_id: 解析結果 或 None}，None 代表「該筆交給同步回退」；
# 任何整包層級的失敗（提交、超時 cancel、結果下載）都必須回全 None、絕不丟例外讓場死掉。
import json

import pytest

import build_summary as bs


class FakeResp:
    def __init__(self, payload=None, text=None, ok=True, status=200):
        self._payload, self.text = payload, text if text is not None else json.dumps(payload or {})
        self.ok, self.status_code = ok, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    """依序回放 post/get 腳本；同時記錄呼叫過的 URL 供斷言（如 cancel）。"""

    def __init__(self, posts, gets):
        self.posts, self.gets, self.urls = list(posts), list(gets), []

    def post(self, url, **kw):
        self.urls.append(("POST", url))
        return self.posts.pop(0)

    def get(self, url, **kw):
        self.urls.append(("GET", url))
        return self.gets.pop(0)


def msg(text):
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 2},
            "content": [{"type": "text", "text": text}]}


def result_line(cid, rtype, message=None):
    return json.dumps({"custom_id": cid, "result": {"type": rtype, "message": message}})


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(bs, "BATCH_POLL_SEC", 0)


REQS = {"s0": ("m", "sys", "user0"), "s1": ("m", "sys", "user1")}


def test_batch_success(monkeypatch):
    fake = FakeRequests(
        posts=[FakeResp({"id": "b1", "processing_status": "in_progress"})],
        gets=[FakeResp({"processing_status": "ended", "results_url": "https://x/results"}),
              FakeResp(text="\n".join([result_line("s0", "succeeded", msg("甲")),
                                       result_line("s1", "succeeded", msg("乙"))]))])
    monkeypatch.setattr(bs, "requests", fake)
    out = bs.call_claude_batch(dict(REQS), 60, "t")
    assert out["s0"]["text"] == "甲" and out["s1"]["text"] == "乙"
    assert out["s0"]["usage"] == {"input_tokens": 1, "output_tokens": 2}


def test_batch_deadline_cancels_and_returns_all_none(monkeypatch):
    fake = FakeRequests(
        posts=[FakeResp({"id": "b1", "processing_status": "in_progress"}),
               FakeResp({})],   # cancel 回應
        gets=[])
    monkeypatch.setattr(bs, "requests", fake)
    out = bs.call_claude_batch(dict(REQS), 0, "t")   # 期限 0 → 立即超時
    assert out == {"s0": None, "s1": None}
    assert ("POST", f"{bs.URL_BATCHES}/b1/cancel") in fake.urls


def test_batch_partial_expired_falls_back_per_item(monkeypatch):
    fake = FakeRequests(
        posts=[FakeResp({"id": "b1", "processing_status": "in_progress"})],
        gets=[FakeResp({"processing_status": "ended", "results_url": "https://x/results"}),
              FakeResp(text="\n".join([result_line("s0", "succeeded", msg("甲")),
                                       result_line("s1", "expired")]))])
    monkeypatch.setattr(bs, "requests", fake)
    out = bs.call_claude_batch(dict(REQS), 60, "t")
    assert out["s0"]["text"] == "甲" and out["s1"] is None


def test_batch_submit_error_returns_all_none(monkeypatch):
    fake = FakeRequests(posts=[FakeResp({"type": "error", "error": {"message": "boom"}}, ok=False, status=400)],
                        gets=[])
    monkeypatch.setattr(bs, "requests", fake)
    assert bs.call_claude_batch(dict(REQS), 60, "t") == {"s0": None, "s1": None}


def test_batch_refusal_or_empty_text_falls_back(monkeypatch):
    refusal = {"stop_reason": "refusal", "content": [], "usage": {}}
    fake = FakeRequests(
        posts=[FakeResp({"id": "b1", "processing_status": "in_progress"})],
        gets=[FakeResp({"processing_status": "ended", "results_url": "https://x/results"}),
              FakeResp(text=result_line("s0", "succeeded", refusal))])
    monkeypatch.setattr(bs, "requests", fake)
    assert bs.call_claude_batch({"s0": ("m", "sys", "u")}, 60, "t") == {"s0": None}


def test_deadline_constants_match_spec():
    # am 25 分（使用者裁定的期限回退）；pm 180 分（需留在 summary.yml timeout 240 分內）
    assert bs.BATCH_DEADLINE_SEC == {"am": 25 * 60, "pm": 180 * 60}
    assert bs.SUMMARY_MODELS == ["claude-sonnet-5"]
    assert bs.MIN_OK_FOR_SYNTH == 2


def test_batch_deadline_budget():
    import time as _t
    now = _t.monotonic()
    # 剛進場：剩餘充裕 → 取場次期限本身（容差 2 秒吃掉 monotonic 經過時間）
    assert abs(bs.batch_deadline("am", now) - 25 * 60) <= 2
    assert abs(bs.batch_deadline("pm", now) - 180 * 60) <= 2
    # 閘門耗掉 100 分：pm 剩 225-100-15=110 分 < 180 分 → 取剩餘預算
    assert abs(bs.batch_deadline("pm", now - 100 * 60) - 110 * 60) <= 2
    # 耗掉 210 分：剩 0 → 跳過 batch
    assert bs.batch_deadline("pm", now - 210 * 60) == 0
    # 耗掉 209.5 分：剩 ~30 秒 < 60 秒門檻 → 同樣跳過
    assert bs.batch_deadline("am", now - int(209.5 * 60)) == 0


def test_write_output_code_version(tmp_path, monkeypatch):
    # 隔離 OUT_DIR 與 ROOT（write_output 會清 data/analyses 舊檔，不能碰真 repo）
    monkeypatch.setattr(bs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "09ac097deadbeef")
    bs.write_output("pm", "2026-08-29", [], {"text": "x", "usage": None})
    d = json.loads((tmp_path / "20260829-pm.json").read_text(encoding="utf-8"))
    assert d["code_version"] == "09ac097"
    # 本機無 GITHUB_SHA → null
    monkeypatch.delenv("GITHUB_SHA")
    bs.write_output("am", "2026-08-29", [], {"text": "x", "usage": None})
    d2 = json.loads((tmp_path / "20260829-am.json").read_text(encoding="utf-8"))
    assert d2["code_version"] is None
