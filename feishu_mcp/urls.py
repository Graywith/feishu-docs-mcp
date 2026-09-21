"""飞书文档链接解析。

QA 日常给的是浏览器链接，不是 token，所以入口统一收敛到这里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# token 字符集：字母数字，实际值通常带 doc/wik 等前缀
_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10,}$")

# /docx/<id>、/wiki/<id>、/mindnote(s)/<id> 等
_PATH_RE = re.compile(r"/(docx|wiki|mindnotes?|docs|sheets|base|file)/([A-Za-z0-9]+)")


@dataclass
class DocRef:
    """一个文档引用。kind 决定后续走哪条读取路径。"""

    kind: str  # docx | wiki | mindnote | docs | sheets | base | unknown
    token: str
    source: str

    @property
    def is_wiki(self) -> bool:
        return self.kind == "wiki"

    @property
    def is_mindnote(self) -> bool:
        return self.kind == "mindnote"


class UnsupportedDocError(ValueError):
    """指向了本 server 读不了的资源类型，消息里说明原因与替代办法。"""


_KIND_MESSAGES = {
    "docs": (
        "这是旧版飞书文档（/docs/ 链接），与新版 docx 接口不通用。"
        "请在飞书里把它升级为新版文档后重试。"
    ),
    "sheets": (
        "这是飞书电子表格（/sheets/ 链接），不是文档。"
        "本 server 目前只支持云文档，表格需要另接 sheets 接口。"
    ),
    "base": (
        "这是多维表格（/base/ 链接）。docx 接口读不到它的内容，"
        "需要改用 bitable 接口。"
    ),
    "file": "这是云盘文件（/file/ 链接），不是可解析的文档正文。",
}


def parse_doc_ref(raw: str) -> DocRef:
    """把链接或裸 token 解析成 DocRef。

    支持：
      https://xxx.feishu.cn/docx/<document_id>
      https://xxx.feishu.cn/wiki/<node_token>
      裸 token
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("文档链接或 token 不能为空。")

    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        match = _PATH_RE.search(parsed.path)
        if not match:
            raise UnsupportedDocError(
                f"无法从链接里识别文档 token：{text}\n"
                "期望形如 https://<企业>.feishu.cn/docx/<id> 或 /wiki/<id>。"
            )
        kind, token = match.group(1), match.group(2)
        if kind == "mindnotes":
            kind = "mindnote"
        if kind in _KIND_MESSAGES:
            raise UnsupportedDocError(_KIND_MESSAGES[kind])
        return DocRef(kind=kind, token=token, source=text)

    if not _TOKEN_RE.match(text):
        raise UnsupportedDocError(
            f"既不是合法链接也不像 token：{text!r}\n"
            "请直接粘贴飞书文档的浏览器链接。"
        )

    # 裸 token：按已知前缀推断类型，无前缀时按 docx 处理并在失败时给出提示
    if text.startswith("wik"):
        return DocRef(kind="wiki", token=text, source=text)
    if text.startswith("shtcn") or text.startswith("sht"):
        raise UnsupportedDocError(_KIND_MESSAGES["sheets"])
    if text.startswith("bascn") or text.startswith("bas"):
        raise UnsupportedDocError(_KIND_MESSAGES["base"])
    return DocRef(kind="docx", token=text, source=text)
