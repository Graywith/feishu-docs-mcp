"""读取文档内嵌画板，并调用已公开的画板节点创建、删除接口。"""

from __future__ import annotations

import re
from typing import Any

from . import docs
from .client import FeishuClient


NODES_PATH = "/open-apis/board/v1/whiteboards/{whiteboard_id}/nodes"


class BoardError(ValueError):
    """画板引用或节点参数不符合可安全处理的形状。"""


def _check_board_id(whiteboard_id: str) -> None:
    if not isinstance(whiteboard_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22,27}", whiteboard_id):
        raise BoardError("whiteboard_id 应为 22～27 位字母数字。")


async def read_document_boards(
    client: FeishuClient, target: str, whiteboard_id: str = ""
) -> list[dict[str, Any]]:
    document_id, _ = await docs.resolve_document_id(client, target)
    blocks = await docs.fetch_blocks(client, document_id)
    boards: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("block_type") != 43:
            continue
        board_data = block.get("board") or {}
        token = board_data.get("token") if isinstance(board_data, dict) else ""
        if not token:
            raise BoardError(f"画板块 {block.get('block_id') or '(未知)'} 没有返回 whiteboard_id。")
        _check_board_id(token)
        if whiteboard_id and token != whiteboard_id:
            continue
        data = await client.get(NODES_PATH.format(whiteboard_id=token))
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            raise BoardError(f"画板 {token} 的节点响应缺少 nodes 数组。")
        boards.append({
            "block_id": block.get("block_id") or "",
            "whiteboard_id": token,
            "nodes": [node for node in nodes if isinstance(node, dict)],
        })
    if whiteboard_id and not boards:
        raise BoardError("指定的 whiteboard_id 不在这篇文档中。")
    return boards


def render_nodes(nodes: list[dict[str, Any]]) -> str:
    if not nodes:
        return "（画板没有节点）"
    by_id = {str(node["id"]): node for node in nodes if node.get("id")}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent_id = str(
            node.get("parent_id")
            or (node.get("mind_map_node") or {}).get("parent_id")
            or (node.get("mind_map") or {}).get("parent_id")
            or ""
        )
        if parent_id and parent_id in by_id:
            children.setdefault(parent_id, []).append(node)
        else:
            roots.append(node)

    lines: list[str] = []
    visited: set[str] = set()

    def emit(node: dict[str, Any], depth: int) -> None:
        node_id = str(node.get("id") or "")
        if node_id in visited:
            return
        visited.add(node_id)
        value = node.get("text") or {}
        label = value.get("text") if isinstance(value, dict) else value
        if not label and isinstance(value, dict):
            paragraphs = (value.get("rich_text") or {}).get("paragraphs") or []
            label = " / ".join(
                "".join(
                    str((element.get("text_element") or element.get("link_element") or {}).get("text") or "")
                    for element in paragraph.get("elements", [])
                )
                for paragraph in paragraphs
            )
        label = " / ".join(str(label or "").splitlines()).strip()
        kind = str(node.get("type") or "unknown")
        lines.append(f"{'  ' * depth}- {label or '（无文字）'} [{kind}] (id:{node_id})")
        syntax = node.get("syntax") or {}
        if isinstance(syntax, dict) and syntax.get("code"):
            indent = "  " * (depth + 1)
            lines.append(f"{indent}```")
            lines.extend(f"{indent}{line}" for line in str(syntax["code"]).splitlines())
            lines.append(f"{indent}```")
        for child in children.get(node_id, []):
            emit(child, depth + 1)

    for root in roots:
        emit(root, 0)
    for node in nodes:
        if str(node.get("id") or "") not in visited:
            emit(node, 0)
    return "\n".join(lines)


async def create_nodes(
    client: FeishuClient, whiteboard_id: str, nodes: list[dict[str, Any]]
) -> dict[str, Any]:
    _check_board_id(whiteboard_id)
    _check_nodes(nodes)
    return await client.post(NODES_PATH.format(whiteboard_id=whiteboard_id), {"nodes": nodes})


def _check_nodes(nodes: list[dict[str, Any]]) -> None:
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 3000:
        raise BoardError("需要 whiteboard_id 和 1～3000 个画板节点。")
    if any(not isinstance(node, dict) or not node.get("type") for node in nodes):
        raise BoardError("每个节点都必须是包含 type 的对象。")


async def replace_nodes(
    client: FeishuClient, whiteboard_id: str, nodes: list[dict[str, Any]],
    expected_ids: list[str],
) -> dict[str, Any]:
    """整板覆盖；飞书会删除原节点并分配新 ID。"""
    _check_board_id(whiteboard_id)
    _check_nodes(nodes)
    if not isinstance(expected_ids, list) or any(
        not isinstance(node_id, str) or not node_id for node_id in expected_ids
    ) or len(set(expected_ids)) != len(expected_ids):
        raise BoardError("expected_ids 必须是当前画板节点 ID 的完整列表。")
    path = NODES_PATH.format(whiteboard_id=whiteboard_id)
    current = (await client.get(path)).get("nodes", [])
    if not isinstance(current, list):
        raise BoardError("画板节点响应缺少 nodes 数组。")
    if {node.get("id") for node in current} != set(expected_ids):
        raise BoardError("画板节点已变化，停止整板覆盖；请重新读取并提供完整节点 ID。")
    return await client.post(path, {"nodes": nodes, "overwrite": True})


async def delete_nodes(
    client: FeishuClient, whiteboard_id: str, ids: list[str]
) -> dict[str, Any]:
    _check_board_id(whiteboard_id)
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
        raise BoardError("需要 whiteboard_id 和 1～100 个节点 id。")
    if any(not isinstance(node_id, str) or not node_id.strip() for node_id in ids):
        raise BoardError("每个节点 id 都必须是非空字符串。")
    return await client.request(
        "DELETE", NODES_PATH.format(whiteboard_id=whiteboard_id) + "/batch_delete",
        json_body={"ids": ids}, write=True,
    )


async def update_theme(
    client: FeishuClient, whiteboard_id: str, theme: str
) -> dict[str, Any]:
    _check_board_id(whiteboard_id)
    if theme not in {"classic", "minimalist_gray", "retro", "vibrant_color", "default"}:
        raise BoardError("主题应为 classic、minimalist_gray、retro、vibrant_color 或 default。")
    return await client.post(
        f"/open-apis/board/v1/whiteboards/{whiteboard_id}/update_theme", {"theme": theme}
    )
