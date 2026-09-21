"""验证限流退避：单应用频控是 HTTP 400 + code 99991400，单文档并发编辑是 429。
只判 429 会漏掉前者，所以两种都要测。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feishu_mcp.client as client_mod
from feishu_mcp.client import FeishuAPIError, FeishuClient


class FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeHTTP:
    """按脚本依次返回响应，记录尝试次数。"""

    def __init__(self, script: list[FakeResponse]) -> None:
        self.script = script
        self.attempts = 0

    async def request(self, method, path, params=None, json=None, headers=None):
        self.attempts += 1
        return self.script[min(self.attempts - 1, len(self.script) - 1)]


class FakeTokens:
    def __init__(self) -> None:
        self.forced = 0

    async def get(self) -> str:
        return "tok"

    async def force_refresh(self) -> str:
        self.forced += 1
        return "tok2"


def build(script):
    c = FeishuClient(token_provider=FakeTokens())
    http = FakeHTTP(script)
    c._client = http
    # 关掉真实等待，让测试跑得快
    c._read_limiter._min_interval = 0
    c._write_limiter._min_interval = 0
    return c, http


def run(coro):
    return asyncio.run(coro)


def test_app_rate_limit_400_is_retried():
    """HTTP 400 + 99991400 必须被识别为频控并重试。"""
    script = [
        FakeResponse(400, {"code": 99991400, "msg": "too many request"}),
        FakeResponse(400, {"code": 99991400, "msg": "too many request"}),
        FakeResponse(200, {"code": 0, "data": {"ok": True}}),
    ]
    c, http = build(script)
    data = run(c.get("/x"))
    assert data == {"ok": True}
    assert http.attempts == 3


def test_doc_concurrent_edit_429_is_retried():
    """单文档并发编辑返回 429，没有 code 字段，也要重试。"""
    script = [
        FakeResponse(429, {}),
        FakeResponse(200, {"code": 0, "data": {"ok": 1}}),
    ]
    c, http = build(script)
    assert run(c.post("/y", {})) == {"ok": 1}
    assert http.attempts == 2


def test_rate_limit_exhausted_raises_readable_error():
    c, http = build([FakeResponse(429, {})])
    try:
        run(c.get("/z"))
        raise AssertionError("重试耗尽应抛错")
    except FeishuAPIError as exc:
        assert "频控" in str(exc)
    assert http.attempts == client_mod.MAX_RETRIES


def test_401_triggers_single_refresh():
    """access_token 失效时刷新一次并重试，且不进入死循环。"""
    tokens = FakeTokens()
    c = FeishuClient(token_provider=tokens)
    http = FakeHTTP([FakeResponse(401, {}), FakeResponse(200, {"code": 0, "data": {"v": 2}})])
    c._client = http
    c._read_limiter._min_interval = 0
    assert run(c.get("/a")) == {"v": 2}
    assert tokens.forced == 1


def test_401_not_retried_forever():
    tokens = FakeTokens()
    c = FeishuClient(token_provider=tokens)
    http = FakeHTTP([FakeResponse(401, {"code": 99991671, "msg": "invalid token"})])
    c._client = http
    c._read_limiter._min_interval = 0
    try:
        run(c.get("/a"))
    except FeishuAPIError:
        pass
    assert tokens.forced == 1, "只应刷新一次，避免死循环"


def test_business_error_hint_attached():
    """错误码要翻译成可操作提示，而不是裸 JSON。"""
    c, _ = build([FakeResponse(403, {"code": 1770032, "msg": "forbidden"})])
    try:
        run(c.get("/b"))
        raise AssertionError("应抛错")
    except FeishuAPIError as exc:
        assert "没有该文档的读取权限" in str(exc)
        assert exc.code == 1770032


def test_paged_follows_page_token():
    responses = [
        FakeResponse(200, {"code": 0, "data": {"items": [1, 2], "has_more": True, "page_token": "p2"}}),
        FakeResponse(200, {"code": 0, "data": {"items": [3], "has_more": False}}),
    ]
    c, http = build(responses)
    assert run(c.paged("/list")) == [1, 2, 3]
    assert http.attempts == 2


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'全部通过' if not failed else str(failed) + ' 个失败'}")
    raise SystemExit(1 if failed else 0)
