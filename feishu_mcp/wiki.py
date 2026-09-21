"""知识库（wiki）节点解析与遍历。

关键点：wiki 链接里的 node_token 不是 document_id。必须先解析出底层
obj_token，再拿它去调 docx 接口。两步、两个权限点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import FeishuClient, FeishuAPIError

GET_NODE_PATH = "/open-apis/wiki/v2/spaces/get_node"
LIST_NODES_PATH = "/open-apis/wiki/v2/spaces/{space_id}/nodes"
CREATE_NODE_PATH = "/open-apis/wiki/v2/spaces/{space_id}/nodes"


@dataclass
class WikiNode:
    node_token: str
    obj_token: str
    obj_type: str
    title: str
    space_id: str
    has_child: bool
    parent_node_token: str = ""


def _node_from_payload(payload: dict[str, Any]) -> WikiNode:
    return WikiNode(
        node_token=payload.get("node_token") or "",
        obj_token=payload.get("obj_token") or "",
        obj_type=payload.get("obj_type") or "",
        title=payload.get("title") or "(无标题)",
        space_id=payload.get("space_id") or "",
        has_child=bool(payload.get("has_child")),
        parent_node_token=payload.get("parent_node_token") or "",
    )


async def get_node(client: FeishuClient, node_token: str) -> WikiNode:
    """把 node_token 解析成节点信息，含底层 obj_token。"""
    data = await client.get(GET_NODE_PATH, token=node_token, obj_type="wiki")
    node = data.get("node") or {}
    if not node:
        raise FeishuAPIError(-1, f"wiki 节点 {node_token} 未返回内容", 200, GET_NODE_PATH)
    return _node_from_payload(node)


async def create_node(
    client: FeishuClient,
    space_id: str,
    *,
    title: str,
    obj_type: str,
    parent_node_token: str = "",
) -> WikiNode:
    """在知识库中创建一个原始云文档节点。"""
    body: dict[str, Any] = {
        "obj_type": obj_type,
        "node_type": "origin",
        "title": title.strip(),
    }
    if parent_node_token.strip():
        body["parent_node_token"] = parent_node_token.strip()
    data = await client.post(CREATE_NODE_PATH.format(space_id=space_id), body)
    node = data.get("node") or {}
    if not node:
        raise FeishuAPIError(-1, "创建知识库节点后接口未返回 node", 200, CREATE_NODE_PATH)
    return _node_from_payload(node)


async def resolve_to_document_id(client: FeishuClient, node_token: str) -> str:
    """wiki node_token -> docx document_id。非 docx 类型给出明确报错。"""
    node = await get_node(client, node_token)
    if node.obj_type not in ("docx", "doc"):
        if node.obj_type == "mindnote":
            raise FeishuAPIError(
                -1,
                f"该知识库节点挂载的是 MindNote（标题：{node.title}）。"
                "请改用 feishu_read_mindnote，或直接让 feishu_read_doc 自动分流。",
                200,
                GET_NODE_PATH,
            )
        raise FeishuAPIError(
            -1,
            f"该知识库节点挂载的是 {node.obj_type or '未知'} 类型（标题：{node.title}），"
            "不是新版文档，docx 接口读不了。",
            200,
            GET_NODE_PATH,
        )
    if not node.obj_token:
        raise FeishuAPIError(-1, f"节点 {node.title} 没有 obj_token", 200, GET_NODE_PATH)
    return node.obj_token


async def list_children(
    client: FeishuClient, space_id: str, parent_node_token: str | None = None
) -> list[WikiNode]:
    path = LIST_NODES_PATH.format(space_id=space_id)
    items = await client.paged(
        path, page_size=50, parent_node_token=parent_node_token
    )
    return [_node_from_payload(item) for item in items]


async def walk_tree(
    client: FeishuClient,
    space_id: str,
    parent_node_token: str | None = None,
    max_depth: int = 3,
) -> list[tuple[int, WikiNode]]:
    """广度优先遍历节点树，返回 (深度, 节点)。

    max_depth 有意保守：知识库可能很大，全量遍历会打满频控。
    """
    results: list[tuple[int, WikiNode]] = []
    frontier = [(0, parent_node_token)]
    while frontier:
        depth, token = frontier.pop(0)
        if depth >= max_depth:
            continue
        for node in await list_children(client, space_id, token):
            results.append((depth, node))
            if node.has_child:
                frontier.append((depth + 1, node.node_token))
    return results


def render_tree(nodes: list[tuple[int, WikiNode]], tenant_domain: str) -> str:
    if not nodes:
        return "（该节点下没有子节点）"
    lines = []
    for depth, node in nodes:
        indent = "  " * depth
        link = f"{tenant_domain}/wiki/{node.node_token}"
        suffix = "" if node.obj_type in ("docx", "doc") else f"  [{node.obj_type or '未知类型'}]"
        lines.append(f"{indent}- {node.title}  <{link}>{suffix}")
    return "\n".join(lines)
