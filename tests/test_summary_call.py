# build_summary 的 Anthropic 呼叫層：空回應攔截、stop_reason 保留、retry 行為（全離線，mock requests）
# 外加 SYS prompt 在 index.html ↔ build_summary.py 兩份副本的逐字一致性守門
import re
from pathlib import Path

import pytest

import build_summary as bs

ROOT = Path(__file__).resolve().parent.parent


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload

    def json(self):
        return self._payload


def _msg(blocks, stop_reason="end_turn"):
    return {"content": blocks, "stop_reason": stop_reason, "usage": {"output_tokens": 8000}}


THINKING_ONLY = [{"type": "thinking", "thinking": "……"}]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(bs.time, "sleep", lambda s: None)
    monkeypatch.setattr(bs, "anth_key", lambda: "test-key")


def _stub_post(monkeypatch, payload):
    calls = []
    monkeypatch.setattr(bs.requests, "post",
                        lambda url, headers, json, timeout: calls.append(json) or FakeResp(payload))
    return calls


# ---------- 空回應攔截（2026-07-27 事故） ----------

def test_text_and_stop_reason_returned(monkeypatch):
    _stub_post(monkeypatch, _msg(THINKING_ONLY + [{"type": "text", "text": "內容"}]))
    res = bs.call_claude("m", "sys", "user")
    assert res["text"] == "內容"
    assert res["stop_reason"] == "end_turn"


def test_thinking_only_response_is_failure(monkeypatch):
    # adaptive thinking 吃滿 max_tokens 時沒有 text block；舊碼回空字串且 ok:true，
    # 空白區塊被拼進彙總 USER，彙總層只好自行宣告「為空白」。現在必須當失敗。
    _stub_post(monkeypatch, _msg(THINKING_ONLY, stop_reason="max_tokens"))
    with pytest.raises(RuntimeError, match="回應無 text 內容"):
        bs.call_claude("m", "sys", "user")


def test_whitespace_only_text_is_failure(monkeypatch):
    _stub_post(monkeypatch, _msg([{"type": "text", "text": "  \n  "}]))
    with pytest.raises(RuntimeError, match="回應無 text 內容"):
        bs.call_claude("m", "sys", "user")


def test_refusal_still_raises(monkeypatch):
    _stub_post(monkeypatch, _msg([{"type": "text", "text": "抱歉"}], stop_reason="refusal"))
    with pytest.raises(RuntimeError, match="婉拒"):
        bs.call_claude("m", "sys", "user")


# ---------- retry 行為 ----------

def test_retry_marks_ok_false_after_two_empty(monkeypatch):
    calls = _stub_post(monkeypatch, _msg(THINKING_ONLY, stop_reason="max_tokens"))
    res = bs.call_claude_retry("m", "sys", "user", "測試份")
    assert res["ok"] is False
    assert res["stop_reason"] is None
    assert "回應無 text 內容" in res["text"]
    assert len(calls) == 2  # 空回應確實有觸發第二次嘗試


def test_retry_recovers_on_second_attempt(monkeypatch):
    n = [0]

    def flaky(url, headers, json, timeout):
        n[0] += 1
        blocks = THINKING_ONLY if n[0] == 1 else [{"type": "text", "text": "第二次成功"}]
        return FakeResp(_msg(blocks))

    monkeypatch.setattr(bs.requests, "post", flaky)
    res = bs.call_claude_retry("m", "sys", "user", "測試份")
    assert res["ok"] is True
    assert res["text"] == "第二次成功"
    assert res["stop_reason"] == "end_turn"


# ---------- SYS prompt 兩份副本逐字一致（CLAUDE.md：index.html 為唯一事實來源） ----------

SYS_PAIRS = [("SUM_SYS_POSTMKT", "SYS_POSTMKT"), ("SUM_SYS_LIVE", "SYS_LIVE"),
             ("SUM_SYS_NEWS", "SYS_NEWS"), ("SUM_SYS_SYNTH", "SYS_SYNTH")]


def _js_const(name: str) -> str:
    """從 index.html 取出 `const <name> = "…" + "…";` 串接後的完整字串。"""
    lines = (ROOT / "index.html").read_text(encoding="utf-8").split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.lstrip().startswith(f"const {name} ="))
    j = i
    while not lines[j].rstrip().endswith(";"):
        j += 1
    return "".join(part for ln in lines[i:j + 1]
                   for part in re.findall(r'"((?:[^"\\]|\\.)*)"', ln))


@pytest.mark.parametrize("js_name,py_name", SYS_PAIRS)
def test_sys_prompt_copies_identical(js_name, py_name):
    assert _js_const(js_name) == getattr(bs, py_name)
