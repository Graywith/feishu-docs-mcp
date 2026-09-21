"""从任意工作目录启动本地 Feishu MCP STDIO Server。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from feishu_mcp.server import main  # noqa: E402


if __name__ == "__main__":
    main()
