"""将飞书 Docx 块级结构转换为 Markdown。

``raw_content`` 仅返回纯文本，无法表达表格、标题层级与待办状态；本模块通过
块结构恢复这些语义。

解析器刻意写得宽容：block_type 枚举以数字为准，但取内容时先按类型名找
payload，找不到就扫描任何带 elements 的字段。飞书新增块类型时不会整篇解析失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BLOCKS_PATH = "/open-apis/docx/v1/documents/{document_id}/blocks"

# block_type 数字 -> 类型名。类型名同时也是 payload 所在的字段名。
BLOCK_TYPE_NAMES: dict[int, str] = {
    1: "page",
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
    18: "bitable",
    19: "callout",
    20: "chat_card",
    21: "diagram",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    26: "iframe",
    27: "image",
    28: "isv",
    29: "mindnote",
    30: "sheet",
    31: "table",
    32: "table_cell",
    33: "view",
    34: "quote_container",
    35: "task",
    36: "okr",
    37: "okr_objective",
    38: "okr_key_result",
    39: "okr_progress",
    40: "add_ons",
    41: "jira_issue",
    42: "wiki_catalog",
    43: "board",
    44: "agenda",
    45: "agenda_item",
    46: "agenda_item_title",
    47: "agenda_item_content",
    48: "link_preview",
    999: "undefined",
}

HEADING_TYPES = {3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9}

# 代码块 language 枚举（数字）-> Markdown 语言标注。只列常用的。
CODE_LANGUAGES: dict[int, str] = {
    1: "plaintext", 2: "abap", 3: "ada", 4: "apache", 5: "apex",
    6: "assembly", 7: "bash", 8: "csharp", 9: "cpp", 10: "c",
    11: "cobol", 12: "css", 13: "coffeescript", 14: "d", 15: "dart",
    16: "delphi", 17: "django", 18: "dockerfile", 19: "erlang", 20: "fortran",
    22: "go", 23: "groovy", 24: "html", 25: "htmlbars", 26: "http",
    27: "haskell", 28: "json", 29: "java", 30: "javascript", 31: "julia",
    32: "kotlin", 33: "latex", 34: "lisp", 36: "lua", 37: "matlab",
    38: "makefile", 39: "markdown", 40: "nginx", 41: "objectivec",
    43: "php", 44: "perl", 45: "powershell", 46: "prolog", 48: "python",
    49: "r", 50: "rst", 51: "ruby", 52: "rust", 53: "sas",
    54: "scss", 55: "sql", 56: "scala", 57: "scheme", 59: "shell",
    60: "swift", 61: "thrift", 62: "typescript", 63: "vbscript",
    64: "visualbasic", 65: "xml", 66: "yaml", 67: "cmake", 68: "diff",
    69: "gherkin", 70: "graphql", 71: "opengl", 72: "properties",
    73: "solidity", 74: "toml",
}


@dataclass
class Block:
    block_id: str
    block_type: int
    payload: dict[str, Any]
    children: list[str] = field(default_factory=list)
    parent_id: str = ""

    @property
    def type_name(self) -> str:
        return BLOCK_TYPE_NAMES.get(self.block_type, f"type_{self.block_type}")


def _index_blocks(items: list[dict[str, Any]]) -> tuple[dict[str, Block], list[str]]:
    """把平铺的块列表建成索引，并找出根块。

    响应里每个块自带 children（子块 id 列表）与 parent_id，据此重建层级；
    parent_id 指向不存在的块时按根处理，避免整篇丢内容。
    """
    blocks: dict[str, Block] = {}
    order: list[str] = []
    for item in items:
        bid = item.get("block_id") or ""
        if not bid:
            continue
        btype = int(item.get("block_type") or 0)
        blocks[bid] = Block(
            block_id=bid,
            block_type=btype,
            payload=item,
            children=list(item.get("children") or []),
            parent_id=item.get("parent_id") or "",
        )
        order.append(bid)

    referenced = {cid for b in blocks.values() for cid in b.children}
    roots = [bid for bid in order if bid not in referenced]
    return blocks, roots


def _escape_md(text: str) -> str:
    """转义会破坏表格或强调语义的字符。"""
    return text.replace(chr(92), chr(92)*2).replace("|", chr(92) + "|")


def _render_elements(elements: list[dict[str, Any]], *, in_table: bool = False) -> str:
    """把富文本 elements 渲染为 Markdown 内联文本。

    元素种类：text_run（普通文字，带样式）、mention_user、mention_doc、
    equation、reminder、file、inline_block 等。未知种类尽量提取可读文字。
    """
    parts: list[str] = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue

        if "text_run" in el:
            run = el["text_run"] or {}
            content = run.get("content") or ""
            if in_table:
                content = _escape_md(content).replace("\n", "<br>")
            style = run.get("text_element_style") or {}
            link = (style.get("link") or {}).get("url") or ""
            if style.get("inline_code"):
                content = f"`{content}`"
            else:
                # 顺序：先内层再外层，避免 ***a*** 这类歧义
                if style.get("bold"):
                    content = f"**{content}**"
                if style.get("italic"):
                    content = f"*{content}*"
                if style.get("strikethrough"):
                    content = f"~~{content}~~"
            if link:
                from urllib.parse import unquote

                content = f"[{content}]({unquote(link)})"
            parts.append(content)

        elif "mention_user" in el:
            user = el["mention_user"] or {}
            parts.append(f"@{user.get('user_id') or '某用户'}")

        elif "mention_doc" in el:
            doc = el["mention_doc"] or {}
            title = doc.get("title") or "关联文档"
            url = doc.get("url") or ""
            from urllib.parse import unquote

            parts.append(f"[{title}]({unquote(url)})" if url else f"[{title}]")

        elif "equation" in el:
            content = (el["equation"] or {}).get("content") or ""
            parts.append(f"${content.strip()}$")

        elif "reminder" in el:
            parts.append("[提醒]")

        elif "inline_file" in el or "file" in el:
            info = el.get("inline_file") or el.get("file") or {}
            parts.append(f"[附件 {info.get('file_token') or ''}]")

        else:
            # 未知元素：提取一层 content 字段，不存在时跳过
            for value in el.values():
                if isinstance(value, dict) and isinstance(value.get("content"), str):
                    parts.append(value["content"])
                    break

    return "".join(parts)


def _payload_of(block: Block) -> dict[str, Any]:
    """取块的内容 payload。

    正常情况下字段名等于类型名（如 text 块的内容在 payload['text']）。
    飞书新增类型或字段名不符预期时，退化为扫描任何带 elements 的字段——
    这样至少文字不会丢。
    """
    named = block.payload.get(block.type_name)
    if isinstance(named, dict):
        return named
    for key, value in block.payload.items():
        if key in ("block_id", "block_type", "parent_id", "children", "comment_ids"):
            continue
        if isinstance(value, dict) and "elements" in value:
            return value
    return {}


def _text_of(block: Block, *, in_table: bool = False) -> str:
    payload = _payload_of(block)
    return _render_elements(payload.get("elements") or [], in_table=in_table)


def _render_table(block: Block, blocks: dict[str, Block], depth: int) -> list[str]:
    """还原表格为 Markdown 表。

    Table 块的 children 是按行优先顺序排列的 TableCell，列数由 property.column_size
    给出。缺少 column_size 时退化为单列输出，保证内容不丢。
    每个 TableCell 自身还有 children（真正的文字块），所以要递归取内容。
    """
    payload = _payload_of(block)
    prop = payload.get("property") or {}
    cells = [blocks[cid] for cid in block.children if cid in blocks]

    try:
        columns = int(prop.get("column_size") or 0)
    except (TypeError, ValueError):
        columns = 0
    if columns <= 0:
        columns = int(prop.get("column_size", 0) or 0) or 1

    if columns <= 1 or not cells:
        # 拿不到列信息：逐格输出，宁可格式退化也不丢内容
        lines: list[str] = []
        for cell in cells:
            text = _cell_text(cell, blocks)
            if text:
                lines.append(f"- {text}")
        return lines or ["（空表格）"]

    rows: list[list[str]] = []
    for start in range(0, len(cells), columns):
        chunk = cells[start : start + columns]
        row = [_cell_text(cell, blocks) for cell in chunk]
        # 末行不足列数时补空，保证 Markdown 表格列对齐
        row += [""] * (columns - len(row))
        rows.append(row)

    if not rows:
        return ["（空表格）"]

    header_row = bool(prop.get("header_row"))
    lines = []
    if header_row:
        head, body = rows[0], rows[1:]
    else:
        # 无表头时造一个占位表头，否则 Markdown 不会渲染成表格
        head = [f"列{i + 1}" for i in range(columns)]
        body = rows

    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "|".join([" --- "] * columns) + "|")
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _cell_text(cell: Block, blocks: dict[str, Block]) -> str:
    """取单元格文字。单元格里可能有多个段落，用 <br> 连接以留在一格内。"""
    pieces: list[str] = []
    for child_id in cell.children:
        child = blocks.get(child_id)
        if child is None:
            continue
        if child.block_type == 31:
            pieces.append("（嵌套表格，见原文档）")
            continue
        text = _text_of(child, in_table=True)
        if child.block_type in HEADING_TYPES and text:
            text = f"**{text}**"
        elif child.block_type == 12 and text:
            text = f"• {text}"
        elif child.block_type == 17 and text:
            payload = _payload_of(child)
            done = bool((payload.get("style") or {}).get("done"))
            text = f"{'[x]' if done else '[ ]'} {text}"
        if text:
            pieces.append(text)
    return "<br>".join(pieces)


# 有独立渲染逻辑的容器块，主循环里跳过默认处理
_CONTAINER_TYPES = {31, 32, 24, 25, 19, 34}


def _render_block(
    block: Block,
    blocks: dict[str, Block],
    depth: int,
    ordered_counters: dict[str, int],
) -> list[str]:
    """渲染单个块（含其子树），返回若干行 Markdown。"""
    btype = block.block_type
    indent = "  " * depth
    out: list[str] = []

    if btype == 1:  # page：文档根，标题单独处理，这里只递归子块
        return _render_children(block, blocks, depth, ordered_counters)

    if btype in HEADING_TYPES:
        level = HEADING_TYPES[btype]
        text = _text_of(block)
        out.append(f"{'#' * min(level, 6)} {text}".rstrip())
        out.append("")
        out.extend(_render_children(block, blocks, depth, ordered_counters))
        return out

    if btype == 2:  # text
        text = _text_of(block)
        out.append(f"{indent}{text}" if text else "")
        out.append("")
        out.extend(_render_children(block, blocks, depth, ordered_counters))
        return out

    if btype == 12:  # bullet
        out.append(f"{indent}- {_text_of(block)}")
        out.extend(_render_children(block, blocks, depth + 1, ordered_counters))
        return out

    if btype == 13:  # ordered
        key = f"{block.parent_id}:{depth}"
        ordered_counters[key] = ordered_counters.get(key, 0) + 1
        out.append(f"{indent}{ordered_counters[key]}. {_text_of(block)}")
        out.extend(_render_children(block, blocks, depth + 1, ordered_counters))
        return out

    if btype == 17:  # todo：保留勾选态
        payload = _payload_of(block)
        done = bool((payload.get("style") or {}).get("done"))
        out.append(f"{indent}- [{'x' if done else ' '}] {_text_of(block)}")
        out.extend(_render_children(block, blocks, depth + 1, ordered_counters))
        return out

    if btype == 14:  # code
        payload = _payload_of(block)
        lang_id = (payload.get("style") or {}).get("language")
        lang = CODE_LANGUAGES.get(int(lang_id), "") if isinstance(lang_id, int) else ""
        body = _render_elements(payload.get("elements") or [])
        out.append(f"{indent}```{lang}")
        out.extend(f"{indent}{line}" for line in body.split("\n"))
        out.append(f"{indent}```")
        out.append("")
        return out

    if btype == 15:  # quote
        out.append(f"{indent}> {_text_of(block)}")
        out.append("")
        return out

    if btype == 22:  # divider
        out.append(f"{indent}---")
        out.append("")
        return out

    if btype in (19, 34):  # callout / quote_container：递归内容并整体加引用前缀
        inner = _render_children(block, blocks, 0, ordered_counters)
        label = "提示" if btype == 19 else "引用"
        out.append(f"{indent}> **[{label}]**")
        while inner and not inner[-1].strip():
            inner.pop()
        for line in inner:
            out.append(f"{indent}> {line}" if line else f"{indent}>")
        out.append("")
        return out

    if btype == 31:  # table
        out.append("")
        out.extend(_render_table(block, blocks, depth))
        out.append("")
        return out

    if btype in (24, 25):  # grid / grid_column：分栏，按顺序平铺
        return _render_children(block, blocks, depth, ordered_counters)

    if btype == 27:  # image：不下载二进制，留 token 便于后续处理
        payload = _payload_of(block)
        token = payload.get("token") or ""
        out.append(f"{indent}![图片](feishu-image:{token})")
        out.append("")
        return out

    if btype == 18:  # bitable：docx 接口读不到内容，明确说明而非静默丢弃
        payload = _payload_of(block)
        token = payload.get("token") or ""
        out.append(
            f"{indent}> **[多维表格]** docx 接口无法读取其内容。"
            f"token=`{token}`，需改用 bitable 接口读取。"
        )
        out.append("")
        return out

    if btype == 30:  # sheet
        payload = _payload_of(block)
        out.append(
            f"{indent}> **[电子表格]** 内容需另接 sheets 接口。"
            f"token=`{payload.get('token') or ''}`"
        )
        out.append("")
        return out

    if btype in (21, 29, 43):  # diagram / mindnote / board
        out.append(f"{indent}> **[{block.type_name}]** 图形内容，无法转为文本。")
        out.append("")
        return out

    # 兜底：未知类型也尽可能提取文字，避免内容静默丢失
    text = _text_of(block)
    if text:
        out.append(f"{indent}{text}")
        out.append("")
    out.extend(_render_children(block, blocks, depth, ordered_counters))
    return out


# 列表类块：连续同类之间不加空行，换类型时必须加，否则 Markdown 会并成一个列表
_LIST_TYPES = {12, 13, 17}


def _render_children(
    block: Block, blocks: dict[str, Block], depth: int, ordered_counters: dict[str, int]
) -> list[str]:
    out: list[str] = []
    prev_type: int | None = None
    for child_id in block.children:
        child = blocks.get(child_id)
        if child is None or child.block_type == 32:
            # TableCell 由 _render_table 负责，不在这里重复渲染
            continue
        btype = child.block_type
        if (
            prev_type in _LIST_TYPES
            and btype in _LIST_TYPES
            and btype != prev_type
        ):
            out.append("")
        out.extend(_render_block(child, blocks, depth, ordered_counters))
        prev_type = btype
    if prev_type in _LIST_TYPES:
        out.append("")
    return out


def _collapse_blank(lines: list[str]) -> str:
    """压掉连续空行，输出干净的 Markdown。"""
    result: list[str] = []
    for line in lines:
        if not line.strip():
            if result and not result[-1].strip():
                continue
            result.append("")
        else:
            result.append(line.rstrip())
    while result and not result[-1].strip():
        result.pop()
    return "\n".join(result)


def blocks_to_markdown(items: list[dict[str, Any]], title: str = "") -> str:
    """入口：把 GET /blocks 的结果转成 Markdown。"""
    blocks, roots = _index_blocks(items)
    if not blocks:
        return f"# {title}\n\n（文档为空）" if title else "（文档为空）"

    counters: dict[str, int] = {}
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    for root_id in roots:
        lines.extend(_render_block(blocks[root_id], blocks, 0, counters))
    return _collapse_blank(lines)


def summarize_blocks(items: list[dict[str, Any]]) -> str:
    """统计各类型块数量，用于确认解析覆盖率与排查未知类型。"""
    counts: dict[str, int] = {}
    for item in items:
        name = BLOCK_TYPE_NAMES.get(int(item.get("block_type") or 0), f"type_{item.get('block_type')}")
        counts[name] = counts.get(name, 0) + 1
    parts = [f"{name}×{n}" for name, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return f"共 {len(items)} 块：" + "、".join(parts)
