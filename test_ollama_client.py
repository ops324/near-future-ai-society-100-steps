"""
OllamaClient のリトライ／失敗計測（P1-B）の LLM非依存テスト。
実 HTTP は張らず requests.post / time.sleep をモックする。
実行: ./venv/bin/python test_ollama_client.py
"""
import ollama_client as oc

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


class FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise oc.requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


def _client(**kw):
    # retry_backoff=0 は使わず time.sleep をモックするので実時間は消費しない
    return oc.OllamaClient(model="m", **kw)


def _patch(post, sleep=lambda s: None):
    oc.requests.post = post
    oc.time.sleep = sleep


def test_success_no_retry():
    calls = {"n": 0}
    def post(url, json=None, timeout=None):
        calls["n"] += 1
        return FakeResp({"response": "  hi  "})
    _patch(post)
    c = _client()
    out = c.generate("p")
    check("成功時は1回・trim される", out == "hi" and calls["n"] == 1)
    check("成功時は failure/empty ともに0", c.failure_count == 0 and c.empty_response_count == 0)
    check("call_count が増える", c.call_count == 1)


def test_retry_then_success():
    calls = {"n": 0}
    def post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise oc.requests.exceptions.ConnectionError("down")
        return FakeResp({"response": "ok"})
    sleeps = []
    _patch(post, sleep=lambda s: sleeps.append(s))
    c = _client(max_retries=3, retry_backoff=1.5)
    out = c.generate("p")
    check("3回目で成功し値を返す", out == "ok" and calls["n"] == 3)
    check("成功なので failure_count=0", c.failure_count == 0)
    check("失敗2回ぶん指数バックオフで sleep", sleeps == [1.5, 3.0])


def test_exhaust_retries_counts_failure():
    calls = {"n": 0}
    def post(url, json=None, timeout=None):
        calls["n"] += 1
        raise oc.requests.exceptions.ConnectionError("down")
    _patch(post)
    c = _client(max_retries=2)
    out = c.generate("p")
    check("リトライ使い切りは空文字（サイレントに落とさない）", out == "")
    check("総試行 = 1 + max_retries", calls["n"] == 3)
    check("failure_count=1（run_meta に残る）", c.failure_count == 1)


def test_empty_response_counted_no_retry():
    calls = {"n": 0}
    def post(url, json=None, timeout=None):
        calls["n"] += 1
        return FakeResp({"response": ""})
    _patch(post)
    c = _client(max_retries=3)
    out = c.generate("p")
    check("空応答はリトライしない（同seedで無意味）", out == "" and calls["n"] == 1)
    check("empty_response_count=1・failure ではない",
          c.empty_response_count == 1 and c.failure_count == 0)


def test_non_network_exception_no_retry():
    calls = {"n": 0}
    class BadResp(FakeResp):
        def json(self):
            raise ValueError("bad json")
    def post(url, json=None, timeout=None):
        calls["n"] += 1
        return BadResp({})
    _patch(post)
    c = _client(max_retries=3)
    out = c.generate("p")
    check("非ネットワーク例外はリトライせず失敗計上", out == "" and calls["n"] == 1)
    check("failure_count=1", c.failure_count == 1)


def test_http_error_retried():
    calls = {"n": 0}
    def post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResp({}, status=500)   # raise_for_status → HTTPError（RequestException）
        return FakeResp({"response": "recovered"})
    _patch(post)
    c = _client(max_retries=2)
    out = c.generate("p")
    check("HTTP 5xx もリトライ対象で回復する", out == "recovered" and calls["n"] == 2)


if __name__ == "__main__":
    _orig_post, _orig_sleep = oc.requests.post, oc.time.sleep
    try:
        for fn in [test_success_no_retry, test_retry_then_success,
                   test_exhaust_retries_counts_failure, test_empty_response_counted_no_retry,
                   test_non_network_exception_no_retry, test_http_error_retried]:
            fn()
    finally:
        oc.requests.post, oc.time.sleep = _orig_post, _orig_sleep
    print("\n========================================")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} passed")
    if passed != total:
        print("FAILED:", [n for n, ok in results if not ok])
        raise SystemExit(1)
    print("ALL PASS")
