"""用假 client 验证写入链路：分片顺序、幂等 token、根块 id、上限拦截。

不发真实请求，只断言我们发出的请求形状对不对。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feishu_mcp.md_to_blocks import (
    MAX_BLOCKS_PER_CALL,
    WriteError,
    create_document,
    write_markdown,
)


class FakeClient:
    """记录所有 post 调用，convert 按行数造出假块。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self._lock = asyncio.Lock()

    async def post(self, path: str, body: dict, **params):
        self.calls.append((path, body, params))
        if path.endswith("/blocks/convert"):
            lines = [l for l in body["content"].split("\n") if l.strip()]
            ids = [f"tmp{i}" for i in range(len(lines))]
            return {
                "first_level_block_ids": ids,
                "blocks": [{"block_id": i, "block_type": 2} for i in ids],
            }
        if path == "/open-apis/docx/v1/documents":
            return {"document": {"document_id": "doc_new_1", "title": body["title"]}}
        return {}

    def create_doc_lock(self):
        return self._lock


def run(coro):
    return asyncio.run(coro)


def test_descendant_uses_document_id_as_root_block():
    """根节点的 block_id 必须就是 document_id。"""
    c = FakeClient()
    run(write_markdown(c, "DOCID", "hello"))
    writes = [x for x in c.calls if "descendant" in x[0]]
    assert len(writes) == 1
    assert writes[0][0] == "/open-apis/docx/v1/documents/DOCID/blocks/DOCID/descendant"


def test_children_and_descendants_from_same_convert():
    c = FakeClient()
    run(write_markdown(c, "D", "a\n\nb\n\nc"))
    convert = [x for x in c.calls if "convert" in x[0]][0][1]
    write = [x for x in c.calls if "descendant" in x[0]][0][1]
    assert write["children_id"] == ["tmp0", "tmp1", "tmp2"]
    assert [b["block_id"] for b in write["descendants"]] == write["children_id"]
    assert convert["content_type"] == "markdown"


def test_idempotency_token_present_and_unique():
    c = FakeClient()
    big = "\n\n".join(f"段落 {i}" for i in range(MAX_BLOCKS_PER_CALL * 2))
    run(write_markdown(c, "D", big))
    tokens = [p["client_token"] for _, _, p in c.calls if "client_token" in p]
    assert len(tokens) >= 2, "长文应分成多次写入"
    assert len(set(tokens)) == len(tokens), "client_token 必须每次不同"


def test_append_keeps_chunk_order():
    """追加到末尾：片段按原顺序写。"""
    c = FakeClient()
    big = "\n\n".join(f"段落 {i}" for i in range(MAX_BLOCKS_PER_CALL * 2))
    run(write_markdown(c, "D", big, index=-1))
    contents = [b["content"] for p, b, _ in c.calls if "convert" in p]
    assert len(contents) >= 2
    assert "段落 0" in contents[0], "首片应含最前面的内容"
    assert contents == sorted(contents, key=lambda s: int(s.split()[1]))


def test_insert_at_start_reverses_chunks():
    """插到开头：必须倒序写，否则片段顺序会颠倒。"""
    c = FakeClient()
    big = "\n\n".join(f"段落 {i}" for i in range(MAX_BLOCKS_PER_CALL * 2))
    run(write_markdown(c, "D", big, index=0))
    contents = [b["content"] for p, b, _ in c.calls if "convert" in p]
    assert "段落 0" in contents[-1], "index=0 时最后写的应是最前面的内容"
    # index 在 body 里，每片都用 0；倒序写入使最终顺序正确
    indexes = {b["index"] for _, b, _ in c.calls if "index" in b}
    assert indexes == {0}


def test_empty_content_rejected():
    c = FakeClient()
    try:
        run(write_markdown(c, "D", "   \n\n  "))
        raise AssertionError("空内容应被拒绝")
    except WriteError:
        pass


def test_create_document_normalizes_title():
    c = FakeClient()
    doc = run(create_document(c, "# 我的  标题\n"))
    assert doc["document_id"] == "doc_new_1"
    body = [b for p, b, _ in c.calls if p == "/open-apis/docx/v1/documents"][0]
    assert body["title"] == "我的 标题"
    assert "folder_token" not in body, "未指定文件夹时不应发送 folder_token"


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
