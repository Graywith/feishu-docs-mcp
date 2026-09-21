"""飞书思维笔记（MindNote）节点读取与创建/更新。

MindNote 与 docx 是两个独立的 OpenAPI 域。Wiki 链接需要先解析底层
obj_token；该 token 才是 mindnote_id，不能直接拿 wiki node_token 调接口。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from . import config
from .client import FeishuClient
from .urls import UnsupportedDocError, parse_doc_ref
from .wiki import WikiNode, create_node as create_wiki_node, get_node


NODES_PATH = "/open-apis/mindnote/v1/mindnotes/{mindnote_id}/nodes"
HIGHLIGHTS = {"red", "yellow", "pink", "blue", "cyan", "olive", "grey"}


class MindnoteError(ValueError):
    """MindNote 输入或响应不符合可安全处理的形状。"""


@dataclass
class MindnoteRef:
    mindnote_id: str
    title: str = ""
    source: str = ""
    wiki_node_token: str = ""

    @property
    def url(self) -> str:
        if self.source.startswith(("http://", "https://")):
            return self.source
        return config.mindnote_url(self.mindnote_id)


async def maybe_resolve_target(client: FeishuClient, target: str) -> MindnoteRef | None:
    """若目标明确指向 MindNote 则返回引用，否则返回 None。

    裸 token 在通用文档入口中存在歧义，不在这里猜测；显式 MindNote 工具
    会通过 ``resolve_target`` 将裸 token 当作 mindnote_id。
    """
    text = (target or "").strip()
    if not text.startswith(("http://", "https://")):
        return None

    ref = parse_doc_ref(text)
    if ref.kind == "mindnote":
        return MindnoteRef(ref.token, source=text)
    if not ref.is_wiki:
        return None

    node = await get_node(client, ref.token)
    if node.obj_type != "mindnote":
        return None
    if not node.obj_token:
        raise MindnoteError(f"MindNote 节点 {node.title} 没有返回 obj_token。")
    return MindnoteRef(
        mindnote_id=node.obj_token,
        title=node.title,
        source=text,
        wiki_node_token=node.node_token,
    )


async def resolve_target(client: FeishuClient, target: str) -> MindnoteRef:
    """把 MindNote URL、Wiki URL 或裸 token 解析成 mindnote_id。"""
    text = (target or "").strip()
    if not text:
        raise MindnoteError("MindNote 链接或 token 不能为空。")

    if not text.startswith(("http://", "https://")):
        # 借用统一解析器完成 token 字符集与长度校验；显式 MindNote 工具里
        # 裸 token 的语义由工具本身确定，不按 docx 猜测。
        validated = parse_doc_ref(text)
        return MindnoteRef(validated.token, source=text)

    resolved = await maybe_resolve_target(client, text)
    if resolved is not None:
        return resolved

    ref = parse_doc_ref(text)
    if ref.is_wiki:
        node = await get_node(client, ref.token)
        raise UnsupportedDocError(
            f"该知识库节点底层类型是 {node.obj_type or '未知'}，不是 MindNote。"
        )
    raise UnsupportedDocError("目标不是 MindNote 链接；期望 /mindnote/、/mindnotes/ 或 MindNote 的 Wiki 链接。")


async def list_nodes(client: FeishuClient, mindnote_id: str) -> list[dict[str, Any]]:
    data = await client.get(NODES_PATH.format(mindnote_id=mindnote_id))
    nodes = data.get("nodes") or []
    if not isinstance(nodes, list):
        raise MindnoteError("MindNote 接口返回的 nodes 不是数组。")
    return [node for node in nodes if isinstance(node, dict)]


async def read(client: FeishuClient, target: str) -> dict[str, Any]:
    ref = await resolve_target(client, target)
    nodes = await list_nodes(client, ref.mindnote_id)
    return {"ref": ref, "nodes": nodes, "markdown": render_nodes(nodes)}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _styled(content: str, style: dict[str, Any] | None) -> str:
    if not content or not isinstance(style, dict):
        return content
    if style.get("inline_code"):
        content = f"`{content.replace('`', '\\`')}`"
    if style.get("bold"):
        content = f"**{content}**"
    if style.get("italic"):
        content = f"*{content}*"
    if style.get("strikethrough"):
        content = f"~~{content}~~"
    return content


def render_elements(elements: Any) -> str:
    """把 MindNote 富文本元素转成尽量保真的 Markdown。"""
    if not isinstance(elements, list):
        return ""
    parts: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        kind = element.get("element_type") or ""
        style = element.get("style") if isinstance(element.get("style"), dict) else None
        if kind == "text":
            payload = element.get("text") or {}
            content = payload.get("content") if isinstance(payload, dict) else payload
            parts.append(_styled(_clean_text(content), style))
        elif kind == "link":
            payload = element.get("link") or {}
            if isinstance(payload, dict):
                label = _clean_text(
                    payload.get("text") or payload.get("title") or payload.get("url")
                )
                url = _clean_text(payload.get("url") or payload.get("href"))
                parts.append(f"[{label}]({url})" if label and url else label or url)
        elif kind == "user":
            payload = element.get("mention_user") or {}
            if isinstance(payload, dict):
                name = _clean_text(payload.get("name") or payload.get("user_name"))
                user_id = _clean_text(
                    payload.get("open_id") or payload.get("user_id") or payload.get("union_id")
                )
                parts.append("@" + (name or user_id or "用户"))
        elif kind == "doc":
            payload = element.get("mention_doc") or {}
            if isinstance(payload, dict):
                title = _clean_text(payload.get("title") or payload.get("name") or "文档")
                url = _clean_text(payload.get("url"))
                token = _clean_text(payload.get("token") or payload.get("obj_token"))
                parts.append(f"[{title}]({url})" if url else f"@{title}({token})" if token else f"@{title}")
        else:
            # 新增元素类型时优先保住可见文字，不把整个未知 JSON 泄漏到文档中。
            for payload_name in ("text", "link", "mention_user", "mention_doc"):
                payload = element.get(payload_name)
                if isinstance(payload, dict):
                    fallback = _clean_text(
                        payload.get("content") or payload.get("text") or payload.get("title")
                    )
                    if fallback:
                        parts.append(fallback)
                        break
    return "".join(parts)


def render_nodes(nodes: list[dict[str, Any]]) -> str:
    """按 parent_id 还原为 Markdown 树，并保留后续更新需要的 node_id。"""
    if not nodes:
        return "（思维笔记为空）"

    node_by_id = {
        str(node.get("node_id")): node
        for node in nodes
        if node.get("node_id") not in (None, "")
    }
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent_id = str(node.get("parent_id") or "")
        if not parent_id or parent_id not in node_by_id:
            roots.append(node)
        else:
            children.setdefault(parent_id, []).append(node)

    lines: list[str] = []
    visited: set[str] = set()

    def emit(node: dict[str, Any], depth: int) -> None:
        node_id = str(node.get("node_id") or "")
        if node_id and node_id in visited:
            return
        if node_id:
            visited.add(node_id)
        title = render_elements(node.get("texts")) or "（空节点）"
        marker = "- [x]" if node.get("finish") is True else "-"
        suffix: list[str] = []
        if node.get("highlight"):
            suffix.append(f"高亮:{node['highlight']}")
        if node_id:
            suffix.append(f"node_id:{node_id}")
        meta = f"  `{' · '.join(suffix)}`" if suffix else ""
        lines.append(f"{'  ' * depth}{marker} {title}{meta}")
        note = render_elements(node.get("notes"))
        if note:
            lines.append(f"{'  ' * (depth + 1)}> 备注：{note}")
        for child in children.get(node_id, []):
            emit(child, depth + 1)

    for root in roots:
        emit(root, 0)
    # 异常环或孤立数据也要输出，不能静默丢失。
    for node in nodes:
        if str(node.get("node_id") or "") not in visited:
            emit(node, 0)
    return "\n".join(lines)


async def upsert_text_node(
    client: FeishuClient,
    mindnote_id: str,
    *,
    text: str,
    parent_id: str = "",
    node_id: str = "",
    note: str = "",
    highlight: str = "",
    finish: bool | None = None,
    client_token: str = "",
) -> dict[str, Any]:
    """创建一个纯文本子节点，或更新一个已有节点。"""
    text = text.strip()
    parent_id, node_id = parent_id.strip(), node_id.strip()
    if not text:
        raise MindnoteError("节点正文不能为空。")
    if bool(parent_id) == bool(node_id):
        raise MindnoteError("新建节点只传 parent_id；更新节点只传 node_id，两者必须且只能提供一个。")
    if highlight and highlight not in HIGHLIGHTS:
        raise MindnoteError(f"highlight 只支持：{', '.join(sorted(HIGHLIGHTS))}。")

    node: dict[str, Any] = {
        "texts": [{"element_type": "text", "text": {"content": text}}]
    }
    node["node_id" if node_id else "parent_id"] = node_id or parent_id
    if note.strip():
        node["notes"] = [
            {"element_type": "text", "text": {"content": note.strip()}}
        ]
    if highlight:
        node["highlight"] = highlight
    if finish is not None:
        node["finish"] = finish

    token = client_token.strip() or str(uuid4())
    data = await client.post(
        NODES_PATH.format(mindnote_id=mindnote_id),
        {"client_token": token, "nodes": [node]},
    )
    return {
        "client_token": data.get("client_token") or token,
        "ids": data.get("ids") or [],
        "mode": "update" if node_id else "create",
    }


async def create_root_text_node(
    client: FeishuClient,
    mindnote_id: str,
    *,
    text: str,
    client_token: str = "",
) -> dict[str, Any]:
    """为空白 MindNote 创建首个根节点（不带 parent_id/node_id）。"""
    text = text.strip()
    if not text:
        raise MindnoteError("根节点正文不能为空。")
    token = client_token.strip() or str(uuid4())
    data = await client.post(
        NODES_PATH.format(mindnote_id=mindnote_id),
        {
            "client_token": token,
            "nodes": [
                {"texts": [{"element_type": "text", "text": {"content": text}}]}
            ],
        },
    )
    return {"client_token": data.get("client_token") or token, "ids": data.get("ids") or []}


async def create_in_same_wiki(
    client: FeishuClient,
    placement_target: str,
    *,
    title: str,
    root_text: str = "主题",
    child_texts: list[str] | None = None,
) -> dict[str, Any]:
    """在一个现有 Wiki 节点旁新建 MindNote，并填入简单节点树。"""
    ref = parse_doc_ref(placement_target)
    if not ref.is_wiki:
        raise MindnoteError("新建 MindNote 需要一个 Wiki 链接，用于确定知识库与创建位置。")
    title = title.strip()
    if not title:
        raise MindnoteError("MindNote 标题不能为空。")

    placement = await get_node(client, ref.token)
    created: WikiNode = await create_wiki_node(
        client,
        placement.space_id,
        title=title,
        obj_type="mindnote",
        parent_node_token=placement.parent_node_token,
    )
    if not created.obj_token:
        raise MindnoteError("MindNote 已创建，但接口没有返回 mindnote_id。")

    nodes = await list_nodes(client, created.obj_token)
    roots = [node for node in nodes if not node.get("parent_id")]
    if roots and roots[0].get("node_id"):
        root_id = str(roots[0]["node_id"])
        await upsert_text_node(
            client,
            created.obj_token,
            text=root_text.strip() or title,
            node_id=root_id,
        )
    else:
        root_result = await create_root_text_node(
            client,
            created.obj_token,
            text=root_text.strip() or title,
            client_token=f"feishu-mcp-root-{created.obj_token}",
        )
        if not root_result["ids"]:
            raise MindnoteError("MindNote 已创建，但创建根节点后接口未返回节点 ID。")
        root_id = str(root_result["ids"][0])

    child_ids: list[str] = []
    for index, child_text in enumerate(child_texts or [], start=1):
        text = child_text.strip()
        if not text:
            continue
        result = await upsert_text_node(
            client,
            created.obj_token,
            text=text,
            parent_id=root_id,
            client_token=f"feishu-mcp-create-{created.obj_token}-{index}",
        )
        child_ids.extend(str(item) for item in result["ids"])

    return {
        "node": created,
        "root_id": root_id,
        "child_ids": child_ids,
    }
