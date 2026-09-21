"""Wiki 文本附件读取的离线测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feishu_mcp.attachments import decode_text_file, read_text_attachment
from feishu_mcp.urls import UnsupportedDocError


class FakeClient:
    async def get(self, path: str, **params):
        return {
            "node": {
                "node_token": "WikFile123456",
                "obj_token": "FileToken123456",
                "obj_type": "file",
                "title": "回归说明.md",
                "space_id": "sp1",
            }
        }

    async def download_bytes(self, path: str, *, max_bytes: int):
        assert path.endswith("/files/FileToken123456/download")
        return b"\xef\xbb\xbf# UTF-8\n\ncontent", {"content-type": "text/markdown"}


def run(coro):
    return asyncio.run(coro)


def test_read_markdown_attachment_from_wiki():
    result = run(
        read_text_attachment(
            FakeClient(), "https://x.feishu.cn/wiki/WikFile123456"
        )
    )
    assert result["title"] == "回归说明.md"
    assert result["content"].startswith("# UTF-8")
    assert result["encoding"] == "utf-8-sig"


def test_decode_gb18030_fallback():
    text, encoding = decode_text_file("中文内容".encode("gb18030"))
    assert text == "中文内容" and encoding == "gb18030"


def test_reject_binary_content():
    try:
        decode_text_file(b"a\x00b")
        raise AssertionError("应拒绝包含 NUL 的二进制内容")
    except UnsupportedDocError:
        pass


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
