"""跑全部离线测试。不需要凭证，不发网络请求。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SUITES = [
    "tests/test_offline.py",
    "tests/test_write_mock.py",
    "tests/test_ratelimit_mock.py",
    "tests/test_auth_mock.py",
    "tests/test_mindnote_mock.py",
    "tests/test_attachment_mock.py",
    "tests/test_board_mock.py",
]

def main() -> int:
    failed = []
    for suite in SUITES:
        print(f"\n=== {suite} ===")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / suite)], cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env,
        )
        print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        if result.returncode != 0:
            failed.append(suite)
    print("\n" + "=" * 40)
    if failed:
        print(f"失败的套件：{', '.join(failed)}")
        return 1
    print(f"{len(SUITES)} 个套件全部通过")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
