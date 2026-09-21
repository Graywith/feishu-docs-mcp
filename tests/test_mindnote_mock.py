"""MindNote 离线测试：URL/Wiki 解析、树渲染与写请求形状。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feishu_mcp.mindnote import (
    MindnoteError,
    NODES_PATH,
    create_in_same_wiki,
    create_root_text_node,
    list_nodes,
    render_elements,
    render_nodes,
    resolve_target,
    upsert_text_node,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, path: str, **params):
        self.calls.append(("GET", path, params))
        if path.endswith("/spaces/get_node"):
            return {
                "node": {
                    "node_token": "Wik123456789",
                    "obj_token": "Mind123456789",
                    "obj_type": "mindnote",
                    "title": "路线图",
                    "space_id": "sp1",
                }
            }
        if "/mindnotes/MindNew123456/nodes" in path:
            return {"nodes": []}
        return {"nodes": [{"node_id": "r", "texts": []}]}

    async def post(self, path: str, body: dict, **params):
        self.calls.append(("POST", path, body))
        if path.endswith("/spaces/sp1/nodes"):
            return {
                "node": {
                    "space_id": "sp1",
                    "node_token": "WikNew123456",
                    "obj_token": "MindNew123456",
                    "obj_type": "mindnote",
                    "title": body["title"],
                    "url": "https://x.feishu.cn/wiki/WikNew123456",
                }
            }
        return {"client_token": body["client_token"], "ids": ["new1"]}


def run(coro):
    return asyncio.run(coro)


def test_resolve_direct_and_wiki_links():
    c = FakeClient()
    direct = run(resolve_target(c, "https://x.feishu.cn/mindnotes/Mind123456789#mindmap"))
    assert direct.mindnote_id == "Mind123456789"
    wiki = run(resolve_target(c, "https://x.feishu.cn/wiki/Wik123456789#mindmap"))
    assert wiki.mindnote_id == "Mind123456789" and wiki.title == "路线图"


def test_list_uses_mindnote_v1_path():
    c = FakeClient()
    nodes = run(list_nodes(c, "Mind123456789"))
    assert nodes[0]["node_id"] == "r"
    assert c.calls[0][1] == NODES_PATH.format(mindnote_id="Mind123456789")


def test_render_tree_keeps_ids_notes_and_styles():
    nodes = [
        {
            "node_id": "root",
            "texts": [{"element_type": "text", "text": {"content": "根节点"}}],
        },
        {
            "node_id": "child",
            "parent_id": "root",
            "texts": [
                {
                    "element_type": "text",
                    "text": {"content": "子节点"},
                    "style": {"bold": True},
                }
            ],
            "notes": [{"element_type": "text", "text": {"content": "备注"}}],
            "finish": True,
            "highlight": "yellow",
        },
    ]
    md = render_nodes(nodes)
    assert "- 根节点" in md and "  - [x] **子节点**" in md
    assert "node_id:child" in md and "备注：备注" in md and "高亮:yellow" in md
    assert render_elements([{"element_type": "user", "mention_user": {"name": "小王"}}]) == "@小王"


def test_create_and_update_payloads():
    c = FakeClient()
    created = run(
        upsert_text_node(
            c,
            "Mind123456789",
            text="新增分支",
            parent_id="root",
            highlight="blue",
            finish=False,
        )
    )
    body = c.calls[-1][2]
    assert created["mode"] == "create" and body["nodes"][0]["parent_id"] == "root"
    assert body["nodes"][0]["texts"][0]["text"]["content"] == "新增分支"
    assert body["nodes"][0]["highlight"] == "blue"

    updated = run(
        upsert_text_node(c, "Mind123456789", text="改名", node_id="child", note="新备注")
    )
    update_node = c.calls[-1][2]["nodes"][0]
    assert updated["mode"] == "update" and update_node["node_id"] == "child"
    assert "parent_id" not in update_node and update_node["notes"]


def test_create_whole_mindnote_in_same_wiki():
    c = FakeClient()
    result = run(
        create_in_same_wiki(
            c,
            "https://x.feishu.cn/wiki/Wik123456789",
            title="新思维笔记",
            root_text="中心主题",
            child_texts=["分支1", "分支2"],
        )
    )
    assert result["node"].obj_token == "MindNew123456", result
    create_call = next(call for call in c.calls if call[1].endswith("/spaces/sp1/nodes"))
    assert create_call[2]["obj_type"] == "mindnote", create_call
    node_writes = [
        call
        for call in c.calls
        if call[0] == "POST" and "/mindnotes/MindNew123456/nodes" in call[1]
    ]
    assert len(node_writes) == 3, c.calls
    assert "node_id" not in node_writes[0][2]["nodes"][0], node_writes
    assert "parent_id" not in node_writes[0][2]["nodes"][0], node_writes
    assert node_writes[1][2]["nodes"][0]["parent_id"] == "new1", node_writes


def test_write_validation():
    c = FakeClient()
    for kwargs in (
        {"text": "x"},
        {"text": "x", "parent_id": "p", "node_id": "n"},
        {"text": "x", "parent_id": "p", "highlight": "purple"},
        {"text": " ", "parent_id": "p"},
    ):
        try:
            run(upsert_text_node(c, "M", **kwargs))
            raise AssertionError(f"应拒绝：{kwargs}")
        except MindnoteError:
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
