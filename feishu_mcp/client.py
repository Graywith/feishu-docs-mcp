"""飞书 OpenAPI HTTP 封装：限流、退避重试、错误码翻译。

飞书的限流有两种表现，必须都处理：
- 单应用频控：HTTP 400 + code 99991400（读约 5 QPS，写约 3 QPS）
- 单文档并发编辑：HTTP 429（创建/更新/删除块共享这个额度）
只判 429 会漏掉前者。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import config
from .auth import AuthError, TokenProvider

MAX_RETRIES = 5
RATE_LIMIT_CODE = 99991400

# 将常见飞书错误码映射为稳定、可读的工具错误消息
ERROR_HINTS: dict[int, str] = {
    1770032: (
        "当前身份没有该文档的读取权限。确认你的飞书账号能在浏览器里打开这篇文档；"
        "若是他人文档，需对方分享给你。"
    ),
    1770036: "同一目录下有并发创建操作，文件夹被锁。创建文档需串行，稍后重试。",
    1770039: "folder_token 无效，请检查文件夹链接是否正确。",
    1770040: "没有该文件夹的操作权限，请让文件夹所有者授予编辑权限。",
    1770006: "descendant 的 children_id 里混入了孙级块，只能放第一级子块。",
    1770001: "请求参数不合法，请检查 document_id 与 block_id。",
    131006: "wiki 节点不存在或无权访问，请确认知识库链接与访问权限。",
    99991672: "应用缺少本接口所需的权限点，请在开放平台「权限管理」开通并发布新版本。",
    99991679: "当前用户 token 缺少本接口所需权限。请确认用户身份权限已开通并发布，再重新授权。",
    2890005: "当前身份没有该画板的操作权限。确认文档访问权、画板权限，并重新进行用户授权。",
}


class FeishuAPIError(RuntimeError):
    def __init__(self, code: int, msg: str, status: int, path: str) -> None:
        hint = ERROR_HINTS.get(code)
        text = f"飞书接口失败 code={code} msg={msg} (HTTP {status}, {path})"
        if hint:
            text += f"\n{hint}"
        super().__init__(text)
        self.code = code
        self.msg = msg
        self.status = status


class _RateLimiter:
    """简单令牌桶：把请求间隔拉开，减少触发频控的概率。"""

    def __init__(self, qps: float) -> None:
        self._min_interval = 1.0 / qps
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self._min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._last = now


class FeishuClient:
    """所有飞书 API 调用的唯一出口。"""

    def __init__(self, token_provider: TokenProvider | None = None) -> None:
        self._tokens = token_provider or TokenProvider()
        self._client: httpx.AsyncClient | None = None
        # 读写用不同的桶：写接口额度更紧
        self._read_limiter = _RateLimiter(4.0)
        self._write_limiter = _RateLimiter(2.5)
        # 建文档必须串行，否则同目录并发会撞 1770036
        self._create_lock = asyncio.Lock()

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=config.OPEN_BASE, timeout=60)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> dict[str, Any]:
        """发一次请求并返回响应体的 data 部分。失败抛 FeishuAPIError。"""
        limiter = self._write_limiter if write else self._read_limiter
        client = await self._http()
        token = await self._tokens.get()
        refreshed = False

        for attempt in range(MAX_RETRIES):
            await limiter.acquire()
            resp = await client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )

            try:
                body = resp.json()
            except ValueError:
                body = {}
            code = body.get("code")

            # 频控：单应用是 400+99991400，单文档并发编辑是 429
            if resp.status_code == 429 or code == RATE_LIMIT_CODE:
                if attempt == MAX_RETRIES - 1:
                    raise FeishuAPIError(
                        code or 429,
                        "触发飞书频控且重试耗尽，请降低调用频率后重试",
                        resp.status_code,
                        path,
                    )
                await asyncio.sleep(min(2**attempt * 0.5, 8.0))
                continue

            # access_token 失效：刷新一次再重试，只试一轮避免死循环
            if resp.status_code == 401 and not refreshed:
                token = await self._tokens.force_refresh()
                refreshed = True
                continue

            if code not in (0, None):
                raise FeishuAPIError(int(code), body.get("msg") or "", resp.status_code, path)
            if resp.status_code >= 400:
                raise FeishuAPIError(
                    -1, body.get("msg") or resp.text[:300], resp.status_code, path
                )
            return body.get("data") or {}

        raise FeishuAPIError(-1, "重试耗尽", 0, path)

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self.request("GET", path, params=clean)

    async def post(self, path: str, body: dict[str, Any], **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self.request("POST", path, params=clean, json_body=body, write=True)

    async def download_bytes(
        self, path: str, *, max_bytes: int = 8 * 1024 * 1024
    ) -> tuple[bytes, dict[str, str]]:
        """下载二进制响应到内存，并在读取过程中执行大小限制。"""
        client = await self._http()
        token = await self._tokens.get()
        refreshed = False

        for attempt in range(MAX_RETRIES):
            await self._read_limiter.acquire()
            async with client.stream(
                "GET",
                path,
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
            ) as resp:
                if resp.status_code == 401 and not refreshed:
                    token = await self._tokens.force_refresh()
                    refreshed = True
                    continue

                raw_error = b""
                code: int | None = None
                if resp.status_code >= 400:
                    raw_error = await resp.aread()
                    try:
                        body = resp.json()
                    except ValueError:
                        body = {}
                    value = body.get("code")
                    code = int(value) if value is not None else None
                    if resp.status_code == 429 or code == RATE_LIMIT_CODE:
                        if attempt == MAX_RETRIES - 1:
                            raise FeishuAPIError(
                                code or 429,
                                "触发飞书频控且重试耗尽，请降低调用频率后重试",
                                resp.status_code,
                                path,
                            )
                        await asyncio.sleep(min(2**attempt * 0.5, 8.0))
                        continue
                    raise FeishuAPIError(
                        code or -1,
                        body.get("msg") or raw_error[:300].decode("utf-8", errors="replace"),
                        resp.status_code,
                        path,
                    )

                declared = int(resp.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise FeishuAPIError(
                        -1,
                        f"附件大小 {declared} 字节，超过允许的 {max_bytes} 字节",
                        resp.status_code,
                        path,
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FeishuAPIError(
                            -1,
                            f"附件超过允许的 {max_bytes} 字节",
                            resp.status_code,
                            path,
                        )
                    chunks.append(chunk)
                return b"".join(chunks), dict(resp.headers)

        raise FeishuAPIError(-1, "下载重试耗尽", 0, path)

    async def paged(
        self, path: str, *, page_size: int = 500, item_key: str = "items", **params: Any
    ) -> list[dict[str, Any]]:
        """翻完所有分页，返回合并后的列表。"""
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            data = await self.get(
                path, page_size=page_size, page_token=page_token, **params
            )
            items.extend(data.get(item_key) or [])
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                return items

    def create_doc_lock(self) -> asyncio.Lock:
        return self._create_lock
