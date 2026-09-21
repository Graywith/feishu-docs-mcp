"""读取挂载在 Wiki 节点下的文本附件。"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .client import FeishuClient
from .urls import UnsupportedDocError, parse_doc_ref
from .wiki import WikiNode, get_node


DOWNLOAD_PATH = "/open-apis/drive/v1/files/{file_token}/download"
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024


def decode_text_file(data: bytes) -> tuple[str, str]:
    """优先 UTF-8，兼容常见的 Windows/中文文本编码。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            if "\x00" in text:
                raise UnicodeDecodeError(encoding, data, 0, 1, "包含 NUL，疑似二进制文件")
            return text, encoding
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocError("附件无法按 UTF-8 或 GB18030 解码，可能不是纯文本文件。")


async def read_wiki_file_node(
    client: FeishuClient, node: WikiNode, source: str
) -> dict[str, Any]:
    if node.obj_type != "file":
        raise UnsupportedDocError(f"Wiki 节点底层类型是 {node.obj_type or '未知'}，不是附件文件。")
    suffix = PurePosixPath(node.title).suffix.lower()
    if suffix not in TEXT_EXTENSIONS:
        raise UnsupportedDocError(
            f"附件《{node.title}》是 {suffix or '未知'} 格式；当前直接读取仅支持 .md/.markdown/.txt。"
        )
    content, headers = await client.download_bytes(
        DOWNLOAD_PATH.format(file_token=node.obj_token),
        max_bytes=MAX_TEXT_FILE_BYTES,
    )
    text, encoding = decode_text_file(content)
    return {
        "title": node.title,
        "file_token": node.obj_token,
        "source": source,
        "content": text,
        "encoding": encoding,
        "size": len(content),
        "content_type": headers.get("content-type", ""),
    }


async def read_text_attachment(client: FeishuClient, target: str) -> dict[str, Any]:
    ref = parse_doc_ref(target)
    if not ref.is_wiki:
        raise UnsupportedDocError("读取附件需要提供指向附件的 Wiki 链接。")
    return await read_wiki_file_node(client, await get_node(client, ref.token), target)
