"""验证 token 持久化与刷新。

重点：refresh_token 是一次性的，每次刷新必须把新值覆盖写回，
否则下次用旧值会拿到 20073。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feishu_mcp.auth as auth_mod
from feishu_mcp import config
from feishu_mcp.auth import AuthError, TokenProvider, TokenSet, load_tokens, save_tokens


def _use_temp_token_file():
    tmp = Path(tempfile.mkdtemp()) / "token.json"
    config.TOKEN_FILE = tmp
    return tmp


def run(coro):
    return asyncio.run(coro)


def test_roundtrip_persistence():
    _use_temp_token_file()
    tokens = TokenSet("a1", "r1", time.time() + 7200, time.time() + 86400, "docx:document")
    save_tokens(tokens)
    back = load_tokens()
    assert back.access_token == "a1" and back.refresh_token == "r1"
    assert back.scope == "docx:document"


def test_corrupt_token_file_is_tolerated():
    path = _use_temp_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_tokens() is None, "损坏的 token 文件应视为未授权，而不是崩溃"


def test_expired_detection_uses_margin():
    # 还有 60 秒就过期：按已过期处理，避免请求中途失效
    soon = TokenSet("a", "r", time.time() + 60)
    assert soon.expired()
    later = TokenSet("a", "r", time.time() + 7200)
    assert not later.expired()


def test_refresh_persists_new_refresh_token(monkeypatch=None):
    """刷新后新的 refresh_token 必须落盘，旧值不能留下。"""
    path = _use_temp_token_file()
    save_tokens(TokenSet("old_access", "old_refresh", time.time() - 10))

    calls = []

    async def fake_post(payload):
        calls.append(payload)
        return {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
            "refresh_token_expires_in": 86400,
        }

    original = auth_mod._post_token
    auth_mod._post_token = fake_post
    try:
        config.load_dotenv()
        import os

        os.environ["FEISHU_APP_ID"] = "cli_test"
        os.environ["FEISHU_APP_SECRET"] = "secret_test"

        provider = TokenProvider()
        token = run(provider.get())
        assert token == "new_access"
        assert calls[0]["grant_type"] == "refresh_token"
        assert calls[0]["refresh_token"] == "old_refresh"

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["refresh_token"] == "new_refresh", "新 refresh_token 必须覆盖写回"
        assert on_disk["access_token"] == "new_access"
    finally:
        auth_mod._post_token = original


def test_refresh_keeps_old_token_when_response_omits_it():
    """响应没带新 refresh_token 时保留旧值，避免把唯一凭证写丢。"""
    path = _use_temp_token_file()
    save_tokens(TokenSet("a", "keep_me", time.time() - 10))

    async def fake_post(payload):
        return {"access_token": "a2", "expires_in": 7200}

    original = auth_mod._post_token
    auth_mod._post_token = fake_post
    try:
        import os

        os.environ["FEISHU_APP_ID"] = "cli_test"
        os.environ["FEISHU_APP_SECRET"] = "secret_test"
        run(TokenProvider().get())
        assert json.loads(path.read_text(encoding="utf-8"))["refresh_token"] == "keep_me"
    finally:
        auth_mod._post_token = original


def test_parallel_providers_refresh_only_once():
    """多个 MCP 进程的等价场景：等锁者必须重读已刷新的 token。"""
    _use_temp_token_file()
    save_tokens(TokenSet("old_access", "single_use", time.time() - 10))
    calls = []

    async def fake_post(payload):
        calls.append(payload)
        await asyncio.sleep(0.05)
        return {
            "access_token": "shared_access",
            "refresh_token": "rotated_once",
            "expires_in": 7200,
        }

    original = auth_mod._post_token
    auth_mod._post_token = fake_post
    try:
        import os

        os.environ["FEISHU_APP_ID"] = "cli_test"
        os.environ["FEISHU_APP_SECRET"] = "secret_test"

        async def gather_tokens():
            return await asyncio.gather(TokenProvider().get(), TokenProvider().get())

        assert run(gather_tokens()) == ["shared_access", "shared_access"]
        assert len(calls) == 1, "共享 refresh_token 只能使用一次"
    finally:
        auth_mod._post_token = original


def test_no_token_file_gives_actionable_error():
    _use_temp_token_file()
    import os

    os.environ["FEISHU_APP_ID"] = "cli_test"
    os.environ["FEISHU_APP_SECRET"] = "secret_test"
    try:
        run(TokenProvider().get())
        raise AssertionError("未授权应报错")
    except AuthError as exc:
        assert "login.py" in str(exc), "错误消息应包含重新授权入口"


def test_error_hints_cover_known_codes():
    for code, needle in ((20071, "重定向 URL"), (20073, "一次性"), (20027, "权限管理")):
        assert needle in auth_mod._explain_token_error(code, "")


def test_pkce_values_valid():
    verifier, challenge = auth_mod.make_pkce()
    assert 43 <= len(verifier) <= 128
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert set(verifier) <= allowed, "code_verifier 字符集不合规"
    assert "=" not in challenge


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
