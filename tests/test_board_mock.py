"""画板节点的离线读取、树形输出和写请求测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feishu_mcp.board import BoardError, create_nodes, delete_nodes, read_document_boards, render_nodes, replace_nodes, update_theme
from feishu_mcp.client import FeishuAPIError


BOARD_ID = "Abcdefghijklmnopqrstuv"


class FakeClient:
    def __init__(self, forbidden: bool = False, empty: bool = False) -> None:
        self.calls: list[tuple] = []
        self.forbidden = forbidden
        self.empty = empty

    async def paged(self, path: str, **params):
        self.calls.append(("PAGED", path, params))
        return [
            {"block_id": "root", "block_type": 1, "children": ["board1"]},
            {"block_id": "board1", "block_type": 43, "board": {"token": BOARD_ID}},
        ]

    async def get(self, path: str, **params):
        self.calls.append(("GET", path, params))
        if self.forbidden:
            raise FeishuAPIError(2890005, "forbidden", 403, path)
        if self.empty:
            return {}
        return {"nodes": [
            {"id": "parent", "type": "section", "text": {"text": "业务流程"}, "children": ["child"]},
            {"id": "child", "type": "text_shape", "parent_id": "parent", "text": {"text": "审批通过"}},
        ]}

    async def post(self, path: str, body: dict, **params):
        self.calls.append(("POST", path, body, params))
        return {"ids": ["new1"], "client_token": "request-123456"}

    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"client_token": "delete-123456"}


def run(coro):
    return asyncio.run(coro)


def test_read_embedded_board_and_render_parent_child_tree():
    client = FakeClient()
    boards = run(read_document_boards(client, "https://x.feishu.cn/docx/Document1234567890"))
    assert len(boards) == 1
    assert boards[0]["whiteboard_id"] == BOARD_ID
    assert client.calls[1][1] == f"/open-apis/board/v1/whiteboards/{BOARD_ID}/nodes"
    markdown = render_nodes(boards[0]["nodes"])
    assert "- 业务流程 [section]" in markdown
    assert "  - 审批通过 [text_shape]" in markdown


def test_read_empty_board_response():
    boards = run(read_document_boards(FakeClient(empty=True), "Document1234567890"))
    assert boards[0]["nodes"] == []
    assert render_nodes(boards[0]["nodes"]) == "（画板没有节点）"


def test_render_rich_text_and_syntax_source():
    nodes = [
        {"id": "rich", "type": "text_shape", "text": {"rich_text": {"paragraphs": [
            {"elements": [{"element_type": 0, "text_element": {"text": "关键判断"}}]}
        ]}}},
        {"id": "code", "type": "group", "syntax": {"code": "A --> B\nB --> C"}},
    ]
    markdown = render_nodes(nodes)
    assert "关键判断" in markdown
    assert "A --> B\n  B --> C" in markdown


def test_render_mind_map_parent_tree():
    nodes = [
        {"id": "z1:2", "type": "mind_map", "text": {"text": "根节点"},
         "mind_map_node": {"children": ["z1:10"]}},
        {"id": "z1:10", "type": "mind_map", "text": {"text": "子节点"},
         "mind_map_node": {"parent_id": "z1:2"}, "mind_map": {"parent_id": "z1:2"}},
    ]
    markdown = render_nodes(nodes)
    assert "- 根节点 [mind_map]" in markdown
    assert "  - 子节点 [mind_map]" in markdown


def test_permission_error_is_not_hidden():
    try:
        run(read_document_boards(FakeClient(forbidden=True), "Document1234567890"))
        raise AssertionError("画板权限不足应报错")
    except FeishuAPIError as exc:
        assert exc.code == 2890005
        assert "画板" in str(exc)

    missing_scope = FeishuAPIError(99991679, "missing board scope", 400, "/board")
    assert "重新授权" in str(missing_scope)


def test_create_and_delete_use_documented_request_shapes():
    client = FakeClient()
    node = {"type": "text_shape", "x": 10, "y": 20, "text": {"text": "新增说明"}}
    assert run(create_nodes(client, BOARD_ID, [node]))["ids"] == ["new1"]
    assert client.calls[0][2] == {"nodes": [node]}
    assert run(delete_nodes(client, BOARD_ID, ["new1"]))["client_token"] == "delete-123456"
    assert client.calls[1][0] == "DELETE"
    assert client.calls[1][1].endswith("/nodes/batch_delete")
    assert client.calls[1][2]["json_body"] == {"ids": ["new1"]}
    assert client.calls[1][2]["write"] is True


def test_replace_requires_matching_existing_ids_and_overwrites_all_nodes():
    client = FakeClient()
    node = {"type": "text_shape", "text": {"text": "更新后的整张画板"}}
    try:
        run(replace_nodes(client, BOARD_ID, [node], ["wrong-id"]))
        raise AssertionError("当前节点与预期不符时不能覆盖画板")
    except BoardError:
        pass
    assert not any(call[0] == "POST" for call in client.calls)

    client = FakeClient()
    run(replace_nodes(client, BOARD_ID, [node], ["parent", "child"]))
    assert client.calls[1][0] == "POST"
    assert client.calls[1][2] == {"nodes": [node], "overwrite": True}


def test_write_validation_rejects_empty_or_invalid_input():
    client = FakeClient()
    for operation in (
        create_nodes(client, BOARD_ID, []),
        create_nodes(client, BOARD_ID, [{"x": 1}]),
        delete_nodes(client, BOARD_ID, []),
        delete_nodes(client, BOARD_ID, [""]),
        create_nodes(client, "bad/id", [{"type": "text_shape"}]),
    ):
        try:
            run(operation)
            raise AssertionError("无效写入参数应被拒绝")
        except BoardError:
            pass
    assert not client.calls


def test_update_theme_uses_documented_endpoint():
    client = FakeClient()
    run(update_theme(client, BOARD_ID, "classic"))
    assert client.calls[0][1] == f"/open-apis/board/v1/whiteboards/{BOARD_ID}/update_theme"
    assert client.calls[0][2] == {"theme": "classic"}
    try:
        run(update_theme(client, BOARD_ID, "invalid"))
        raise AssertionError("未知主题应被拒绝")
    except BoardError:
        pass


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    raise SystemExit(1 if failed else 0)
