"""注册飞书文档工具并启动 STDIO MCP 服务。

删除操作不属于本服务的能力范围。
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import __version__, attachments, config, docs, mindnote, wiki
from .auth import AuthError
from .client import FeishuAPIError, FeishuClient
from .md_to_blocks import (
    WriteError,
    create_document,
    detect_images,
    write_markdown,
)
from .urls import UnsupportedDocError, parse_doc_ref

mcp = MCPServer(
    name="feishu-docs",
    instructions=(
        "读写飞书云文档。需要保留表格、标题层级和待办状态时，使用 "
        "feishu_read_doc 的 structured 模式。"
        "MindNote 链接用 feishu_read_mindnote 读取节点树；写入前先读取并确认 node_id。"
        "不知道文档链接时先用 feishu_search_docs 搜。"
    ),
    version=__version__,
)

_client = FeishuClient()


def _friendly(exc: Exception) -> str:
    """将预期异常转换为适合 MCP 工具输出的消息。"""
    if isinstance(exc, (AuthError, WriteError, UnsupportedDocError, FeishuAPIError)):
        return f"操作失败：{exc}"
    return f"操作失败（{type(exc).__name__}）：{exc}"


@mcp.tool(
    description=(
        "读取飞书云文档内容并返回 Markdown。structured 模式（默认）保留表格、"
        "标题层级与待办勾选态，适合读 PRD；raw 模式更快但表格会被拍平。"
        "支持 /docx/ 与 /wiki/ 链接、裸 document_id，以及 Wiki 中的 Markdown/文本附件。"
    )
)
async def feishu_read_doc(
    target: Annotated[str, Field(description="飞书文档链接，或 document_id / wiki node_token")],
    mode: Annotated[
        Literal["structured", "raw"],
        Field(description="structured 保留表格结构；raw 只要纯文本"),
    ] = "structured",
) -> str:
    try:
        doc_target = target
        wiki_source = ""
        ref = parse_doc_ref(target)
        if ref.is_wiki:
            node = await wiki.get_node(_client, ref.token)
            if node.obj_type == "file":
                result = await attachments.read_wiki_file_node(_client, node, target)
                return (
                    f"来源：<{result['source']}>\n"
                    f"类型：文本附件，文件名：{result['title']}\n"
                    f"大小：{result['size']} 字节，编码：{result['encoding']}\n\n---\n\n"
                    + result["content"]
                )
            if node.obj_type == "mindnote":
                nodes = await mindnote.list_nodes(_client, node.obj_token)
                return (
                    f"来源：<{target}>\n"
                    f"类型：MindNote，节点数：{len(nodes)}\n\n---\n\n"
                    + mindnote.render_nodes(nodes)
                )
            if node.obj_type not in ("doc", "docx"):
                raise UnsupportedDocError(
                    f"该知识库节点底层类型是 {node.obj_type or '未知'}，当前无法读取。"
                )
            doc_target, wiki_source = node.obj_token, target
        else:
            mindnote_ref = await mindnote.maybe_resolve_target(_client, target)
            if mindnote_ref is not None:
                nodes = await mindnote.list_nodes(_client, mindnote_ref.mindnote_id)
                return (
                    f"来源：<{mindnote_ref.url}>\n"
                    f"类型：MindNote，节点数：{len(nodes)}\n\n---\n\n"
                    + mindnote.render_nodes(nodes)
                )

        if mode == "raw":
            result = await docs.read_raw(_client, doc_target)
            source = wiki_source or result["url"]
            header = f"# 文档纯文本\n\n<{source}>\n"
            note = "\n> 注意：raw 模式已丢失表格结构。需要表格请用 structured 模式。\n"
            return f"{header}{note}\n{result['content']}"

        result = await docs.read_structured(_client, doc_target)
        lines = [
            f"来源：<{wiki_source or result['url']}>",
            f"解析：{result['stats']}",
        ]
        if wiki_source:
            lines.append("（已自动把 wiki 节点解析为底层文档）")
        return "\n".join(lines) + "\n\n---\n\n" + result["markdown"]
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(description="取文档标题与版本号，用于确认拿对了文档，或读取前先看规模。")
async def feishu_get_doc_meta(
    target: Annotated[str, Field(description="文档链接或 token")],
) -> str:
    try:
        mindnote_ref = await mindnote.maybe_resolve_target(_client, target)
        if mindnote_ref is not None:
            nodes = await mindnote.list_nodes(_client, mindnote_ref.mindnote_id)
            return "\n".join(
                [
                    f"标题：{mindnote_ref.title or '(MindNote)'}",
                    f"mindnote_id：{mindnote_ref.mindnote_id}",
                    f"节点数：{len(nodes)}",
                    f"链接：{mindnote_ref.url}",
                ]
            )
        document_id, ref = await docs.resolve_document_id(_client, target)
        meta = await docs.get_meta(_client, document_id)
        parts = [
            f"标题：{meta.get('title') or '(无标题)'}",
            f"document_id：{document_id}",
            f"revision_id：{meta.get('revision_id')}",
            f"链接：{config.doc_url(document_id)}",
        ]
        if ref.is_wiki:
            parts.append(f"（原始 wiki node_token：{ref.token}）")
        return "\n".join(parts)
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(
    description=(
        "读取已有飞书思维笔记的全部节点，按 parent_id 还原为 Markdown 树。"
        "支持 /mindnote/、/mindnotes/、指向 MindNote 的 /wiki/ 链接和裸 mindnote_id；"
        "返回 node_id，后续创建子节点或更新节点时要使用。"
    )
)
async def feishu_read_mindnote(
    target: Annotated[str, Field(description="MindNote/Wiki 链接或 mindnote_id")],
) -> str:
    try:
        result = await mindnote.read(_client, target)
        ref = result["ref"]
        return (
            f"来源：<{ref.url}>\n"
            f"标题：{ref.title or '(MindNote)'}\n"
            f"mindnote_id：{ref.mindnote_id}\n"
            f"节点数：{len(result['nodes'])}\n\n---\n\n"
            + result["markdown"]
        )
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(
    description=(
        "在一个现有 Wiki 节点的同级目录新建整篇 MindNote。"
        "placement_target 用于确定知识库位置；children 每行会创建一个一级分支。"
    )
)
async def feishu_create_mindnote(
    placement_target: Annotated[str, Field(description="用于确定创建位置的现有 Wiki 链接")],
    title: Annotated[str, Field(description="新 MindNote 的文档标题")],
    root_text: Annotated[str, Field(description="根节点正文")] = "主题",
    children: Annotated[str, Field(description="可选一级分支，每行一个")] = "",
) -> str:
    try:
        result = await mindnote.create_in_same_wiki(
            _client,
            placement_target,
            title=title,
            root_text=root_text,
            child_texts=children.splitlines(),
        )
        node = result["node"]
        return (
            "已创建 MindNote。\n"
            f"标题：{node.title}\n"
            f"mindnote_id：{node.obj_token}\n"
            f"根节点 ID：{result['root_id']}\n"
            f"链接：{node.url or config.tenant_domain() + '/wiki/' + node.node_token}"
        )
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(
    description=(
        "在已有飞书 MindNote 中新建一个纯文本子节点，或更新一个已有节点。"
        "新建时只传 parent_id；更新时只传 node_id。写入前应先用 feishu_read_mindnote "
        "核对目标与节点 ID。新建整篇 MindNote 请用 feishu_create_mindnote；本工具不删除节点。"
    )
)
async def feishu_upsert_mindnote_node(
    target: Annotated[str, Field(description="MindNote/Wiki 链接或 mindnote_id")],
    text: Annotated[str, Field(description="节点正文，不能为空")],
    parent_id: Annotated[str, Field(description="新建子节点时的父 node_id；更新时留空")] = "",
    node_id: Annotated[str, Field(description="更新已有节点时的 node_id；新建时留空")] = "",
    note: Annotated[str, Field(description="可选备注正文")] = "",
    highlight: Annotated[
        str,
        Field(description="可选高亮：red/yellow/pink/blue/cyan/olive/grey"),
    ] = "",
    finish: Annotated[bool | None, Field(description="可选完成状态；不传则保持默认/原值")] = None,
) -> str:
    try:
        ref = await mindnote.resolve_target(_client, target)
        result = await mindnote.upsert_text_node(
            _client,
            ref.mindnote_id,
            text=text,
            parent_id=parent_id,
            node_id=node_id,
            note=note,
            highlight=highlight,
            finish=finish,
        )
        action = "更新" if result["mode"] == "update" else "创建"
        ids = ", ".join(str(x) for x in result["ids"]) or "（接口未返回 ID）"
        return (
            f"已{action} MindNote 节点。\n"
            f"节点 ID：{ids}\n"
            f"链接：{ref.url}\n"
            f"client_token：{result['client_token']}"
        )
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(
    description="按关键词搜索云文档与知识库节点，返回标题和链接。不知道文档链接时用它。"
)
async def feishu_search_docs(
    keyword: Annotated[str, Field(description="搜索关键词")],
    count: Annotated[int, Field(description="返回条数，1-50", ge=1, le=50)] = 10,
) -> str:
    try:
        found = await docs.search_docs(_client, keyword, count)
    except Exception as exc:
        return _friendly(exc)

    # wiki 搜索接口不如云文档搜索稳定，失败时降级而不是让整个工具报错
    wiki_items: list[dict] = []
    try:
        wiki_items = await docs.search_wiki(_client, keyword)
    except Exception:
        pass
    return docs.render_search_results(found, wiki_items)


@mcp.tool(
    description=(
        "列出知识库空间的节点树，用于批量定位文档。"
        "给 wiki 链接时会自动解析出 space_id 并从该节点往下列。"
    )
)
async def feishu_wiki_tree(
    space_id: Annotated[str, Field(description="知识库 space_id，与 wiki_link 二选一")] = "",
    wiki_link: Annotated[
        str, Field(description="或给一个 wiki 链接，自动解析 space_id 并从该节点往下列")
    ] = "",
    max_depth: Annotated[int, Field(description="遍历深度，默认 2", ge=1, le=5)] = 2,
) -> str:
    try:
        parent_token: str | None = None
        if wiki_link:
            from .urls import parse_doc_ref

            ref = parse_doc_ref(wiki_link)
            if not ref.is_wiki:
                return "wiki_link 需要是 /wiki/ 形式的知识库链接。普通文档请直接用 feishu_read_doc。"
            node = await wiki.get_node(_client, ref.token)
            space_id, parent_token = node.space_id, node.node_token
        if not space_id:
            return "需要提供 space_id 或 wiki_link 之一。"

        nodes = await wiki.walk_tree(_client, space_id, parent_token, max_depth=max_depth)
        return wiki.render_tree(nodes, config.tenant_domain())
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(
    description=(
        "新建飞书文档并写入 Markdown 正文，返回文档链接。"
        "支持标题、列表、表格、代码块、待办、引用。"
        "Markdown 里的图片不会被搬运（需另外上传媒体）。"
    )
)
async def feishu_create_doc(
    title: Annotated[str, Field(description="文档标题，纯文本，1-800 字符")],
    markdown: Annotated[str, Field(description="Markdown 正文")],
    folder_token: Annotated[
        str, Field(description="目标文件夹 token，留空则建在云空间根目录")
    ] = "",
) -> str:
    try:
        document = await create_document(_client, title, folder_token)
        document_id = document["document_id"]
        blocks_written = await write_markdown(_client, document_id, markdown, index=-1)
        url = config.doc_url(document_id)
        lines = [
            f"已创建文档：{document.get('title')}",
            f"链接：{url}",
            f"写入顶层块：{blocks_written} 个",
        ]
        images = detect_images(markdown)
        if images:
            lines.append(
                f"提醒：内容里有 {len(images)} 处图片引用未被搬运"
                "（convert 接口不搬运媒体），需要手动补图。"
            )
        return "\n".join(lines)
    except Exception as exc:
        return _friendly(exc)


@mcp.tool(description="向已有飞书文档追加 Markdown 内容。默认追加到末尾，不改动原有内容。")
async def feishu_append_doc(
    target: Annotated[str, Field(description="目标文档链接或 document_id")],
    markdown: Annotated[str, Field(description="要追加的 Markdown 内容")],
    position: Annotated[
        Literal["end", "start"], Field(description="追加到末尾还是插到开头")
    ] = "end",
) -> str:
    try:
        document_id, _ = await docs.resolve_document_id(_client, target)
        index = -1 if position == "end" else 0
        blocks_written = await write_markdown(_client, document_id, markdown, index=index)
        where = "末尾" if position == "end" else "开头"
        return (
            f"已向文档追加内容（{where}），写入顶层块 {blocks_written} 个。\n"
            f"链接：{config.doc_url(document_id)}"
        )
    except Exception as exc:
        return _friendly(exc)


def main() -> None:
    config.load_dotenv()
    mcp.run()


if __name__ == "__main__":
    main()
