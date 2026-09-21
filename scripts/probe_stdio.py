"""通过 JSON-RPC 验证 STDIO MCP 初始化与工具发现。"""

import json
import subprocess
import sys
from pathlib import Path

PY = sys.executable
ROOT = Path(__file__).resolve().parent.parent


def frame(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


proc = subprocess.Popen(
    [PY, str(ROOT / "run_server.py")],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    cwd="C:\\",  # 从项目外目录启动，验证入口不依赖当前工作目录
)

payload = (
    frame({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    })
    + frame({"jsonrpc": "2.0", "method": "notifications/initialized"})
    + frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
)

try:
    out, err = proc.communicate(payload, timeout=30)
except subprocess.TimeoutExpired:
    proc.kill()
    out, err = proc.communicate()

for line in out.splitlines():
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("id") == 1:
        info = msg.get("result", {})
        print("initialize ->", info.get("protocolVersion"), info.get("serverInfo"))
    if msg.get("id") == 2:
        tools = msg.get("result", {}).get("tools", [])
        print(f"tools ({len(tools)}):", ", ".join(t["name"] for t in tools))

if err.strip():
    print("--- stderr ---", file=sys.stderr)
    print(err.strip()[:800], file=sys.stderr)
