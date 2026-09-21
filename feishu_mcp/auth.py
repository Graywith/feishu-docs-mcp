"""user_access_token 的获取、持久化与自动刷新。

设计要点：
- refresh_token 一次性，每次刷新必须把响应里的新 refresh_token 覆盖写回，
  否则复用旧值会拿到 20073 "The refresh token has been used"。
- 过期时间以响应的 expires_in 为准，不硬编码；提前若干秒主动刷新。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import config

# 提前刷新的余量，避免请求发出后 token 中途过期
REFRESH_MARGIN_SECONDS = 300
TOKEN_LOCK_TIMEOUT_SECONDS = 30


class AuthError(RuntimeError):
    """飞书用户鉴权失败。"""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float = 0.0
    scope: str = ""

    def expired(self, margin: int = REFRESH_MARGIN_SECONDS) -> bool:
        return time.time() >= self.expires_at - margin

    def refresh_usable(self) -> bool:
        if not self.refresh_token:
            return False
        # refresh_token_expires_in 缺失时按可用处理，让服务端来判定
        return self.refresh_expires_at == 0.0 or time.time() < self.refresh_expires_at


def _atomic_write(path: Path, payload: str) -> None:
    """先写临时文件再替换，避免刷新过程中被中断导致 token 文件损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows 上 chmod 能力有限，失败不影响功能
        pass


def save_tokens(tokens: TokenSet) -> None:
    _atomic_write(config.TOKEN_FILE, json.dumps(asdict(tokens), ensure_ascii=False, indent=2))


def load_tokens() -> TokenSet | None:
    if not config.TOKEN_FILE.exists():
        return None
    try:
        raw = json.loads(config.TOKEN_FILE.read_text(encoding="utf-8"))
        return TokenSet(**raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


class _TokenFileLock:
    """跨进程串行化 refresh_token 刷新。

    多个 MCP 客户端或会话可能同时启动 STDIO 进程。飞书的 refresh_token
    为一次性，只有 asyncio.Lock 不足以防止多进程重复使用。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        self._handle = handle
        deadline = time.monotonic() + TOKEN_LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        self.release()
                        raise AuthError("等待其他 MCP 进程刷新飞书 token 超时。")
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _tokens_from_response(data: dict) -> TokenSet:
    access = data.get("access_token") or ""
    if not access:
        raise AuthError(f"飞书未返回 access_token，响应：{data}")
    now = time.time()
    refresh_expires_in = data.get("refresh_token_expires_in") or 0
    return TokenSet(
        access_token=access,
        refresh_token=data.get("refresh_token") or "",
        expires_at=now + float(data.get("expires_in") or 0),
        refresh_expires_at=now + float(refresh_expires_in) if refresh_expires_in else 0.0,
        scope=data.get("scope") or "",
    )


def make_pkce() -> tuple[str, str]:
    """生成 PKCE 的 (code_verifier, code_challenge)，challenge 用 S256。"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def build_authorize_url(client_id: str, code_challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri(),
        "scope": " ".join(config.OAUTH_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{config.AUTHORIZE_URL}?{urlencode(params)}"


async def _post_token(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            config.TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    try:
        data = resp.json()
    except ValueError:
        raise AuthError(f"token 接口返回非 JSON（HTTP {resp.status_code}）：{resp.text[:500]}")

    code = data.get("code")
    if code not in (0, None):
        raise AuthError(_explain_token_error(int(code), data.get("error_description") or data.get("msg") or ""))
    if resp.status_code >= 400:
        raise AuthError(f"token 接口 HTTP {resp.status_code}：{data}")
    # v2 把字段放在顶层，部分环境包在 data 里，两种都兼容
    return data.get("data") if isinstance(data.get("data"), dict) and data.get("access_token") is None else data


def _explain_token_error(code: int, detail: str) -> str:
    hints = {
        20071: (
            "redirect_uri 与授权时不一致。检查开放平台「安全设置 → 重定向 URL」"
            f"是否已添加 {config.redirect_uri()}"
        ),
        20073: "refresh_token 已被使用过（一次性）。请重新执行 python scripts/login.py 授权。",
        20027: "请求的 scope 未在开放平台「权限管理」开通，或应用版本未发布审核通过。",
        20050: "授权码已过期或已被使用（有效期 5 分钟且一次性）。请重新走一次授权。",
    }
    hint = hints.get(code)
    base = f"飞书鉴权失败 code={code}"
    if detail:
        base += f"：{detail}"
    return f"{base}\n{hint}" if hint else base


async def exchange_code(client_id: str, client_secret: str, code: str, code_verifier: str) -> TokenSet:
    data = await _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": config.redirect_uri(),
            "code_verifier": code_verifier,
        }
    )
    tokens = _tokens_from_response(data)
    save_tokens(tokens)
    return tokens


async def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> TokenSet:
    data = await _post_token(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
    )
    tokens = _tokens_from_response(data)
    if not tokens.refresh_token:
        # 响应没带新 refresh_token 时保留旧值，避免把唯一凭证写丢
        tokens.refresh_token = refresh_token
    save_tokens(tokens)
    return tokens


class TokenProvider:
    """按需提供有效的 user_access_token，并串行化刷新。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tokens: TokenSet | None = None

    async def _refresh_shared(
        self, client_id: str, client_secret: str, *, force: bool
    ) -> TokenSet:
        """拿到跨进程锁后重读磁盘，避免复用旧 refresh_token。"""
        lock = _TokenFileLock(config.TOKEN_FILE.with_suffix(".lock"))
        await asyncio.to_thread(lock.acquire)
        try:
            latest = load_tokens() or self._tokens
            if latest is None:
                raise AuthError("无可用 refresh_token，请重新运行 scripts/login.py 授权。")

            # 等锁期间另一进程可能已完成刷新。
            refreshed_elsewhere = (
                self._tokens is not None
                and latest.access_token != self._tokens.access_token
                and not latest.expired()
            )
            if (not force and not latest.expired()) or refreshed_elsewhere:
                self._tokens = latest
                return latest
            if not latest.refresh_usable():
                raise AuthError(
                    "refresh_token 已失效，需重新运行 scripts/login.py 授权。"
                )
            self._tokens = await refresh_tokens(
                client_id, client_secret, latest.refresh_token
            )
            return self._tokens
        finally:
            await asyncio.to_thread(lock.release)

    async def get(self) -> str:
        client_id, client_secret = config.require_credentials()
        async with self._lock:
            if self._tokens is None:
                self._tokens = load_tokens()
            if self._tokens is None:
                raise AuthError(
                    "尚未授权。请先运行：\n"
                    "  .venv/Scripts/python.exe scripts/login.py\n"
                    "完成浏览器授权后会在 cache/token.json 生成凭证。"
                )
            if self._tokens.expired():
                self._tokens = await self._refresh_shared(
                    client_id, client_secret, force=False
                )
            return self._tokens.access_token

    async def force_refresh(self) -> str:
        """收到 401 时调用：丢弃当前 access_token 立刻换新的。"""
        client_id, client_secret = config.require_credentials()
        async with self._lock:
            if self._tokens is None:
                self._tokens = load_tokens()
            self._tokens = await self._refresh_shared(
                client_id, client_secret, force=True
            )
            return self._tokens.access_token
