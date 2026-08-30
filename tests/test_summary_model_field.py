# 產出的 model 欄：自動彙總場的 synthesis 與 six[] 都必須記下「實際呼叫時用的模型」，
# 前端（index.html insightCostText）才能估算費用而不必從程式碼常數回推。
# 全離線（mock requests），免 token 免網路。
import json

import pytest

import build_summary as bs


class FakeResp:
    def __init__(self, payload=None, text=None, ok=True, status=200):
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.ok, self.status_code = ok, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    def __init__(self, posts, gets):
        self.posts, self.gets, self.urls = list(posts), list(gets), []

    def post(self, url, **kw):
        self.urls.append(("POST", url))
        return self.posts.pop(0)

    def get(self, url, **kw):
        self.urls.append(("GET", url))
        return self.gets.pop(0)


def msg(text):
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4,
                                                 "service_tier": "batch"},
            "content": [{"type": "text", "text": text}]}


def result_line(cid, rtype, message=None):
    return json.dumps({"custom_id": cid, "result": {"type": rtype, "message": message}})


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(bs, "BATCH_POLL_SEC", 0)
    monkeypatch.setattr(bs.time, "sleep", lambda s: None)
    monkeypatch.setattr(bs, "anth_key", lambda: "test-key-not-real")


# ---------- batch 路徑 ----------

def test_batch_result_carries_requested_model(monkeypatch):
    fake = FakeRequests(
        posts=[FakeResp({"id": "b1", "processing_status": "in_progress"})],
        gets=[FakeResp({"processing_status": "ended", "results_url": "https://x/results"}),
              FakeResp(text="\n".join([result_line("s0", "succeeded", msg("甲")),
                                       result_line("synth", "succeeded", msg("乙"))]))])
    monkeypatch.setattr(bs, "requests", fake)
    out = bs.call_claude_batch({"s0": ("claude-sonnet-5", "sys", "u0"),
                                "synth": ("claude-opus-4-8", "sys", "u1")}, 60, "t")
    # 逐筆各自記自己的 model，不是整包共用一個
    assert out["s0"]["model"] == "claude-sonnet-5"
    assert out["synth"]["model"] == "claude-opus-4-8"
    # 既有欄位不動
    assert out["s0"]["text"] == "甲" and out["s0"]["stop_reason"] == "end_turn"


# ---------- 同步（回退）路徑 ----------

def test_sync_retry_carries_actual_model(monkeypatch):
    monkeypatch.setattr(bs.requests, "post",
                        lambda url, headers, json, timeout: FakeResp(
                            {"stop_reason": "end_turn", "usage": {"output_tokens": 1},
                             "content": [{"type": "text", "text": "內容"}]}))
    res = bs.call_claude_retry("claude-opus-4-8", "sys", "u", "彙總")
    assert res["ok"] is True and res["model"] == "claude-opus-4-8"


def test_sync_retry_failure_placeholder_also_carries_model(monkeypatch):
    monkeypatch.setattr(bs.requests, "post",
                        lambda url, headers, json, timeout: FakeResp(
                            {"stop_reason": "max_tokens", "usage": {},
                             "content": [{"type": "thinking", "thinking": "…"}]}))
    res = bs.call_claude_retry("claude-sonnet-5", "sys", "u", "測試份")
    assert res["ok"] is False and res["model"] == "claude-sonnet-5"


def test_batch_timeout_fallback_records_sync_model(monkeypatch):
    """batch 超時 → 同步回退：寫進產出的必須是同步那次實際用的 model（降級路徑守門）。"""
    real_requests = bs.requests
    fake = FakeRequests(posts=[FakeResp({"id": "b1", "processing_status": "in_progress"}),
                               FakeResp({})], gets=[])
    monkeypatch.setattr(bs, "requests", fake)
    got = bs.call_claude_batch({"synth": ("claude-opus-4-8", "sys", "u")}, 0, "彙總").get("synth")
    assert got is None   # batch 這一路沒有結果 → 主程式改走 call_claude_retry

    monkeypatch.setattr(bs, "requests", real_requests)   # 同步回退走真模組（post 另行 mock）
    monkeypatch.setattr(bs.requests, "post",
                        lambda url, headers, json, timeout: FakeResp(
                            {"stop_reason": "end_turn", "usage": {"output_tokens": 1},
                             "content": [{"type": "text", "text": "回退產出"}]}))
    synth = {"via": "sync", **bs.call_claude_retry("claude-opus-4-8", "sys", "u", "彙總")}
    assert synth["model"] == "claude-opus-4-8" and synth["via"] == "sync"


# ---------- write_output ----------

def test_write_output_synthesis_has_model(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "OUT_DIR", tmp_path)
    six = [{"page": "postmkt", "model": "claude-sonnet-5", "tag": "Sonnet5",
            "date": "2026-08-29", "ok": True, "via": "batch", "text": "甲",
            "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 2}}]
    bs.write_output("pm", "2026-08-29", six,
                    {"text": "彙總", "usage": {"input_tokens": 5, "output_tokens": 6},
                     "via": "batch", "model": "claude-opus-4-8"})
    out = json.loads((tmp_path / "20260829-pm.json").read_text(encoding="utf-8"))
    assert out["synthesis"]["model"] == "claude-opus-4-8"
    # 既有欄位一個都沒少
    assert set(out["synthesis"]) == {"text", "usage", "via", "model"}
    assert out["six"][0]["model"] == "claude-sonnet-5"
    assert out["dates"] == {"postmkt": "2026-08-29"}


def test_model_values_match_frontend_price_table():
    """寫進產出的 model 字串必須是前端 INSIGHT_PRICES 的鍵，否則費用估算會靜靜消失。"""
    import re
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
    block = html.split("const INSIGHT_PRICES")[1].split("};")[0]
    keys = set(re.findall(r'"([a-z0-9-]+)":\s*\[', block))
    assert bs.SYNTH_MODEL in keys
    assert set(bs.SUMMARY_MODELS) <= keys
