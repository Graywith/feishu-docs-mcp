"""读取 MindNote 并输出节点树，用于连接与授权检查；不执行写操作。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from feishu_mcp import config  # noqa: E402
from feishu_mcp.client import FeishuClient  # noqa: E402
from feishu_mcp.mindnote import read  # noqa: E402


class _AppTokenProvider:
    def __init__(self, token: str) -> None:
        self.token = token

    async def get(self) -> str:
        return self.token

    async def force_refresh(self) -> str:
        return self.token


async def _tenant_token() -> str:
    app_id, app_secret = config.require_credentials()
    async with httpx.AsyncClient(base_url=config.OPEN_BASE, timeout=30) as http:
        response = await http.post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
    payload = response.json()
    token = payload.get("tenant_access_token") or ""
    if response.status_code >= 400 or payload.get("code") not in (0, None) or not token:
        raise RuntimeError(
            f"获取 tenant_access_token 失败：HTTP {response.status_code}, "
            f"code={payload.get('code')}, msg={payload.get('msg')}"
        )
    return token


async def show_mindnote_scopes() -> int:
    app_id, _ = config.require_credentials()
    token = await _tenant_token()
    async with httpx.AsyncClient(base_url=config.OPEN_BASE, timeout=30) as http:
        response = await http.get(
            f"/open-apis/application/v6/applications/{app_id}",
            params={"lang": "zh_cn"},
            headers={"Authorization": f"Bearer {token}"},
        )
    payload = response.json()
    app = ((payload.get("data") or {}).get("app") or {})
    scopes = [
        item
        for item in (app.get("scopes") or [])
        if str(item.get("scope") or "").startswith("mindnote:")
    ]
    print(f"应用 ID：{app_id}")
    print(f"HTTP：{response.status_code}，code={payload.get('code')}，msg={payload.get('msg')}")
    for item in scopes:
        print(f"{item.get('scope')} -> {item.get('token_types')}")
    if not scopes:
        print("当前已发布应用信息中没有 MindNote scope。")
    return 0 if response.status_code < 400 and payload.get("code") in (0, None) else 1


async def main(target: str, as_app: bool = False) -> int:
    provider = _AppTokenProvider(await _tenant_token()) if as_app else None
    client = FeishuClient(token_provider=provider)
    try:
        result = await read(client, target)
        ref = result["ref"]
        print(f"标题：{ref.title or '(MindNote)'}")
        print(f"mindnote_id：{ref.mindnote_id}")
        print(f"节点数：{len(result['nodes'])}")
        print("\n---\n")
        print(result["markdown"])
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    if "--scopes" in sys.argv[1:]:
        raise SystemExit(asyncio.run(show_mindnote_scopes()))
    args = [arg for arg in sys.argv[1:] if arg != "--app"]
    if len(args) != 1:
        print("用法：python scripts/probe_mindnote.py [--app] <MindNote/Wiki 链接>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(args[0], as_app="--app" in sys.argv[1:])))
