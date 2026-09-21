"""集中管理环境变量、端点常量与本地路径。

端点常量集中在此处，便于后续从 OAuth v2 迁移到 v3。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "cache"
TOKEN_FILE = CACHE_DIR / "token.json"

# ---- 域名与端点 ----------------------------------------------------------

OPEN_BASE = "https://open.feishu.cn"
ACCOUNTS_BASE = "https://accounts.feishu.cn"

# 授权页（用户在浏览器里访问）
AUTHORIZE_URL = f"{ACCOUNTS_BASE}/open-apis/authen/v1/authorize"
# 换取 / 刷新 user_access_token。v2 已被官方标为历史版本，
# v3 为 https://accounts.feishu.cn/oauth/v3/token，切换时只改这一行。
TOKEN_URL = f"{OPEN_BASE}/open-apis/authen/v2/oauth/token"

# 申请的权限范围。offline_access 必须带上，否则响应里没有 refresh_token。
OAUTH_SCOPES = [
    "offline_access",
    "docx:document",
    "docx:document:readonly",
    "wiki:wiki:readonly",
    "wiki:node:create",
    "drive:drive:readonly",
    "mindnote:node:read",
    "mindnote:node:create",
]

DEFAULT_OAUTH_PORT = 3000
DEFAULT_REDIRECT_PATH = "/callback"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载器，不覆盖已存在的环境变量。

    全局 MCP 配置不复制凭证；服务统一从项目根目录的 .env 加载。
    """
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def app_id() -> str:
    return _env("FEISHU_APP_ID")


def app_secret() -> str:
    return _env("FEISHU_APP_SECRET")


def oauth_port() -> int:
    raw = _env("FEISHU_OAUTH_PORT")
    try:
        return int(raw) if raw else DEFAULT_OAUTH_PORT
    except ValueError:
        return DEFAULT_OAUTH_PORT


def redirect_uri() -> str:
    return f"http://localhost:{oauth_port()}{DEFAULT_REDIRECT_PATH}"


def tenant_domain() -> str:
    """企业飞书域名，仅用于把 document_id 拼成可点击链接。"""
    raw = _env("FEISHU_TENANT_DOMAIN") or "https://feishu.cn"
    return raw.rstrip("/")


def doc_url(document_id: str) -> str:
    return f"{tenant_domain()}/docx/{document_id}"


def mindnote_url(mindnote_id: str) -> str:
    return f"{tenant_domain()}/mindnote/{mindnote_id}"


def require_credentials() -> tuple[str, str]:
    """取 App ID/Secret，缺失时给出可操作的报错。"""
    load_dotenv()
    aid, secret = app_id(), app_secret()
    if not aid or not secret:
        raise RuntimeError(
            "缺少飞书应用凭证。请在服务根目录的 .env 里设置 "
            "FEISHU_APP_ID 与 FEISHU_APP_SECRET（可参考 .env.example）。"
        )
    return aid, secret
