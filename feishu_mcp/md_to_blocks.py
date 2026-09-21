"""Markdown -> 飞书文档内容。

三步链路：
  1. POST /docx/v1/documents                        建空文档
  2. POST /docx/v1/documents/blocks/convert         Markdown -> 块结构
  3. POST /docx/v1/documents/{id}/blocks/{id}/descendant   写入

约束（不挡住就会写失败）：
- children_id 与 descendants 各限 1~1000，长文必须分片
- 两个字段必须来自同一次 convert，不能跨批次混用
- children_id 只放第一级块，放孙级报 1770006
- 根节点的 block_id 就填 document_id
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from .client import FeishuClient

CREATE_DOC_PATH = "/open-apis/docx/v1/documents"
CONVERT_PATH = "/open-apis/docx/v1/documents/blocks/convert"
DESCENDANT_PATH = "/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}/descendant"

# 官方上限 1000。留出余量，避免边界情况被拒。
MAX_BLOCKS_PER_CALL = 800
TITLE_MAX = 800


class WriteError(RuntimeError):
    """写入失败，消息说明是哪一步、为什么。"""


@dataclass
class ConvertResult:
    first_level_block_ids: list[str]
    descendants: list[dict[str, Any]]


def normalize_title(title: str) -> str:
    """标题必须是纯文本 1~800 字符。去掉换行与 Markdown 标记。"""
    text = re.sub(r"\s+", " ", (title or "").strip())
    text = text.lstrip("#").strip()
    if not text:
        text = "未命名文档"
    return text[:TITLE_MAX]


def _split_markdown(content: str, max_blocks: int = MAX_BLOCKS_PER_CALL) -> list[str]:
    """把长 Markdown 按顶层结构切片，保证不切在表格或代码块中间。

    切分单位是「顶层块」的近似：非缩进、非表格行、非围栏内的行边界。
    估算每个片段的块数用行数近似——偏保守，宁可多分几片。
    """
    lines = content.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_marker = ""
    # 表格与代码块内部不可切分
    safe_break_pending = False

    def flush() -> None:
        nonlocal current
        if current:
            text = "\n".join(current).strip("\n")
            if text.strip():
                chunks.append(text)
            current = []

    for line in lines:
        stripped = line.strip()

        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if fence_match:
            if not in_fence:
                in_fence, fence_marker = True, fence_match.group(1)[:3]
            elif stripped.startswith(fence_marker):
                in_fence = False

        is_table_row = stripped.startswith("|")
        # 只在安全点切：不在围栏内、不在表格行、当前片段已够大
        can_break = (
            not in_fence
            and not is_table_row
            and not stripped.startswith(("  ", "\t"))
            and len(current) >= max_blocks
        )
        if can_break and stripped == "":
            flush()
            continue

        current.append(line)

    flush()
    return chunks or [content]


async def convert_markdown(client: FeishuClient, markdown: str) -> ConvertResult:
    """调 convert 接口把 Markdown 转成块结构。

    响应字段名在不同环境有 blocks / block_id_to_block 两种说法，两者都兼容；
    descendants 必须与 first_level_block_ids 来自同一次调用。
    """
    data = await client.post(CONVERT_PATH, {"content_type": "markdown", "content": markdown})

    first_level = data.get("first_level_block_ids") or []
    descendants = data.get("blocks")
    if descendants is None:
        mapping = data.get("block_id_to_block")
        if isinstance(mapping, dict):
            descendants = list(mapping.values())
    if descendants is None:
        raise WriteError(
            "convert 接口未返回块结构。实际返回的字段："
            f"{sorted(data.keys())}。请对照 scripts/dump_doc.py 的输出核对字段名。"
        )
    if not first_level:
        raise WriteError("convert 接口未返回 first_level_block_ids，内容可能为空或格式不受支持。")
    return ConvertResult(first_level_block_ids=list(first_level), descendants=list(descendants))


def _guard_limits(result: ConvertResult) -> None:
    if len(result.first_level_block_ids) > 1000 or len(result.descendants) > 1000:
        raise WriteError(
            "单次写入块数超过飞书上限 1000（"
            f"first_level={len(result.first_level_block_ids)}, "
            f"descendants={len(result.descendants)}）。内容需要切得更小。"
        )


async def write_markdown(
    client: FeishuClient,
    document_id: str,
    markdown: str,
    *,
    index: int = -1,
) -> int:
    """把 Markdown 写入文档。返回写入的顶层块数。

    index=-1 追加到末尾，0 插到最前。分片写入时逐片顺序追加，保证顺序正确。
    """
    if not markdown.strip():
        raise WriteError("待写入内容为空。")

    chunks = _split_markdown(markdown)
    path = DESCENDANT_PATH.format(document_id=document_id, block_id=document_id)
    total = 0
    # 追加时保持片段顺序即可；插到开头时要倒序写，否则片段顺序会颠倒
    ordered = chunks if index == -1 else list(reversed(chunks))

    for chunk in ordered:
        result = await convert_markdown(client, chunk)
        _guard_limits(result)
        await client.post(
            path,
            {
                "children_id": result.first_level_block_ids,
                "index": index,
                "descendants": result.descendants,
            },
            document_revision_id=-1,
            client_token=str(uuid.uuid4()),
        )
        total += len(result.first_level_block_ids)
    return total


async def create_document(
    client: FeishuClient, title: str, folder_token: str = ""
) -> dict[str, Any]:
    """建空文档。同目录并发创建会撞 1770036，所以整个调用串行化。"""
    body: dict[str, Any] = {"title": normalize_title(title)}
    if folder_token:
        body["folder_token"] = folder_token
    async with client.create_doc_lock():
        data = await client.post(CREATE_DOC_PATH, body)
    document = data.get("document") or {}
    document_id = document.get("document_id")
    if not document_id:
        raise WriteError(f"创建文档未返回 document_id，响应：{data}")
    return document


def detect_images(markdown: str) -> list[str]:
    """找出 Markdown 里的图片引用。

    convert 接口不会自动搬运图片，需单独上传媒体拿 image_key 再回填。
    本轮不实现上传，只把它们报出来，避免用户以为图片已经写进去了。
    """
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
