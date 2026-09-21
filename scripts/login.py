"""一次性 OAuth 授权：拿到 user_access_token 并落盘到 cache/token.json。

用法：
    .venv/Scripts/python.exe scripts/login.py

会起一个本地 HTTP server 接收回调，并打开浏览器。授权完成后后续全自动，
只在 refresh_token 失效时需要重跑。
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from feishu_mcp import config  # noqa: E402
from feishu_mcp.auth import (  # noqa: E402
    AuthError,
    build_authorize_url,
    exchange_code,
    make_pkce,
)

_result: dict[str, str] = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            _result["code"] = query["code"][0]
            _result["state"] = (query.get("state") or [""])[0]
            body = "授权成功，可以关闭这个页面回到终端。"
        else:
            _result["error"] = (query.get("error") or ["unknown"])[0]
            _result["error_description"] = (query.get("error_description") or [""])[0]
            body = f"授权失败：{_result['error']}。请回到终端查看提示。"

        payload = f"<html><meta charset='utf-8'><body><p>{body}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        # 静默，避免 HTTP 日志混进授权提示里
        pass


async def main() -> int:
    try:
        client_id, client_secret = config.require_credentials()
    except RuntimeError as exc:
        print(f"[配置错误] {exc}")
        return 1

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(client_id, challenge, state)
    port = config.oauth_port()

    try:
        httpd = HTTPServer(("localhost", port), _CallbackHandler)
    except OSError as exc:
        print(f"[端口占用] 无法监听 localhost:{port}：{exc}")
        print("请关闭占用该端口的程序，或在 .env 里改 FEISHU_OAUTH_PORT（同时更新开放平台的重定向 URL）。")
        return 1

    thread = threading.Thread(target=httpd.handle_request, daemon=True)
    thread.start()

    print("请在浏览器里完成飞书授权。若没有自动打开，手动访问：")
    print(url)
    print()
    print(f"回调地址：{config.redirect_uri()}")
    print("（该地址必须已在开放平台「安全设置 → 重定向 URL」里添加过）")
    webbrowser.open(url)

    thread.join(timeout=300)
    httpd.server_close()

    if "error" in _result:
        print(f"[授权被拒] {_result['error']} {_result.get('error_description', '')}")
        return 1
    if "code" not in _result:
        print("[超时] 5 分钟内没有收到回调。请重试。")
        return 1
    if _result.get("state") != state:
        print("[安全校验失败] state 不匹配，可能是并发授权或 CSRF。请重试。")
        return 1

    try:
        tokens = await exchange_code(client_id, client_secret, _result["code"], verifier)
    except AuthError as exc:
        print(f"[换取 token 失败] {exc}")
        return 1

    print("授权成功。")
    print(f"token 已保存到 {config.TOKEN_FILE}")
    print(f"授予的权限：{tokens.scope or '(未返回 scope)'}")
    if not tokens.refresh_token:
        print(
            "警告：没有拿到 refresh_token。请确认开放平台已开通 offline_access，"
            "否则 token 过期后需要重新授权。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
