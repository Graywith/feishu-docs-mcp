"""文档读取与搜索的业务门面：把 client / urls / wiki / 解析器串起来。"""

from __future__ import annotations

from typing import Any

from . import config
from .blocks_to_md import BLOCKS_PATH, blocks_to_markdown, summarize_blocks
from .client import FeishuClient
from .urls import DocRef, parse_doc_ref
from .wiki import resolve_to_document_id

DOC_META_PATH = "/open-apis/docx/v1/documents/{document_id}"
RAW_CONTENT_PATH = "/open-apis/docx/v1/documents/{document_id}/raw_content"
SEARCH_PATH = "/open-apis/suite/docs-api/search/object"
WIKI_SEARCH_PATH = "/open-apis/wiki/v1/nodes/search"


async def resolve_document_id(client: FeishuClient, target: str) -> tuple[str, DocRef]:
    """把用户给的链接/token 解析成 docx document_id。

    wiki 链接必须先 get_node 拿 obj_token，不能直接当 document_id 用。
    """
    ref = parse_doc_ref(target)
    if ref.is_mindnote:
        raise UnsupportedDocError("这是 MindNote 链接，请使用 feishu_read_mindnote。")
    if ref.is_wiki:
        return await resolve_to_document_id(client, ref.token), ref
    return ref.token, ref


async def get_meta(client: FeishuClient, document_id: str) -> dict[str, Any]:
    data = await client.get(DOC_META_PATH.format(document_id=document_id))
    return data.get("document") or {}


async def fetch_blocks(client: FeishuClient, document_id: str) -> list[dict[str, Any]]:
    """拉全部块。深度优先分页，翻完所有 page_token。"""
    return await client.paged(
        BLOCKS_PATH.format(document_id=document_id),
        page_size=500,
        document_revision_id=-1,
    )


async def read_structured(client: FeishuClient, target: str) -> dict[str, Any]:
    """结构化读取：保留表格、标题层级与勾选态。"""
    document_id, ref = await resolve_document_id(client, target)
    meta = await get_meta(client, document_id)
    title = meta.get("title") or ""
    items = await fetch_blocks(client, document_id)
    return {
        "document_id": document_id,
        "title": title,
        "revision_id": meta.get("revision_id"),
        "url": config.doc_url(document_id),
        "markdown": blocks_to_markdown(items, title),
        "stats": summarize_blocks(items),
        "was_wiki": ref.is_wiki,
    }


async def read_raw(client: FeishuClient, target: str) -> dict[str, Any]:
    """纯文本快速通道：省一次全量块拉取，但表格会被拍平。"""
    document_id, ref = await resolve_document_id(client, target)
    data = await client.get(RAW_CONTENT_PATH.format(document_id=document_id), lang=0)
    return {
        "document_id": document_id,
        "url": config.doc_url(document_id),
        "content": data.get("content") or "",
        "was_wiki": ref.is_wiki,
    }


async def search_docs(
    client: FeishuClient, keyword: str, count: int = 10
) -> list[dict[str, Any]]:
    """搜索云文档；该接口要求使用 user_access_token。"""
    data = await client.post(
        SEARCH_PATH,
        {"search_key": keyword, "count": max(1, min(count, 50)), "offset": 0,
         "docs_types": ["doc"]},
    )
    return data.get("docs_entities") or []


async def search_wiki(client: FeishuClient, keyword: str) -> list[dict[str, Any]]:
    """搜知识库节点。该接口非公开稳定接口，失败时由调用方降级处理。"""
    data = await client.post(WIKI_SEARCH_PATH, {"query": keyword, "page_size": 10})
    return data.get("items") or []


def render_search_results(
    docs: list[dict[str, Any]], wiki_items: list[dict[str, Any]]
) -> str:
    domain = config.tenant_domain()
    lines: list[str] = []
    if docs:
        lines.append("## 云文档")
        for item in docs:
            token = item.get("docs_token") or ""
            dtype = item.get("docs_type") or "doc"
            title = item.get("title") or "(无标题)"
            lines.append(f"- {title}  <{domain}/{'docx' if dtype in ('doc', 'docx') else dtype}/{token}>")
    if wiki_items:
        lines.append("")
        lines.append("## 知识库")
        for item in wiki_items:
            title = item.get("title") or "(无标题)"
            token = item.get("node_id") or item.get("node_token") or ""
            lines.append(f"- {title}  <{domain}/wiki/{token}>")
    if not lines:
        return "没有找到匹配的文档。换个关键词，或确认这些文档在你的飞书账号下可见。"
    return "\n".join(lines)
