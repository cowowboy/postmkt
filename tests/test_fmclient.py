# fmclient 共用 client：token 必要性、api_get 重試/限流/錯誤語意（全離線，mock requests）
import pytest

import fmclient


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": 200, "data": [{"x": 1}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(fmclient.time, "sleep", lambda s: None)


def test_token_optional_and_required(monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    assert fmclient.token() == ""
    with pytest.raises(RuntimeError):
        fmclient.token(required=True)
    monkeypatch.setenv("FINMIND_TOKEN", "  abc  ")
    assert fmclient.token(required=True) == "abc"


def test_api_get_success(monkeypatch):
    calls = []
    monkeypatch.setattr(fmclient.requests, "get",
                        lambda url, params, timeout: calls.append(params) or FakeResp())
    assert fmclient.api_get("SomeDataset", start_date="2026-07-24") == [{"x": 1}]
    assert calls[0]["dataset"] == "SomeDataset"


def test_api_get_status_null_treated_ok(monkeypatch):
    # FinMind 部分回應 status 缺省（None）＝正常
    monkeypatch.setattr(fmclient.requests, "get",
                        lambda url, params, timeout: FakeResp(payload={"data": [1, 2]}))
    assert fmclient.api_get("D") == [1, 2]


def test_api_get_retries_then_raises(monkeypatch):
    n = [0]

    def flaky(url, params, timeout):
        n[0] += 1
        raise OSError("boom")

    monkeypatch.setattr(fmclient.requests, "get", flaky)
    with pytest.raises(RuntimeError, match="重試後仍失敗"):
        fmclient.api_get("D", retries=3)
    assert n[0] == 3


def test_api_get_retry_recovers(monkeypatch):
    n = [0]

    def flaky_then_ok(url, params, timeout):
        n[0] += 1
        if n[0] == 1:
            raise OSError("transient")
        return FakeResp()

    monkeypatch.setattr(fmclient.requests, "get", flaky_then_ok)
    assert fmclient.api_get("D") == [{"x": 1}]
    assert n[0] == 2


def test_api_get_rate_limit_waits_rate_wait(monkeypatch):
    waits = []
    monkeypatch.setattr(fmclient.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setattr(fmclient.requests, "get",
                        lambda url, params, timeout: FakeResp(status_code=429))
    with pytest.raises(RuntimeError, match="rate limited"):
        fmclient.api_get("D", retries=2)
    assert waits == [fmclient.RATE_WAIT]


def test_api_get_bad_status_msg(monkeypatch):
    monkeypatch.setattr(fmclient.requests, "get",
                        lambda url, params, timeout: FakeResp(payload={"status": 400, "msg": "bad token"}))
    with pytest.raises(RuntimeError, match="bad token"):
        fmclient.api_get("D", retries=1)
