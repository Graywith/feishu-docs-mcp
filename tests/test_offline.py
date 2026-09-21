"""解析、格式转换和 URL 处理的离线回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_doc import build
from feishu_mcp.blocks_to_md import blocks_to_markdown, summarize_blocks
from feishu_mcp.md_to_blocks import _split_markdown, detect_images, normalize_title
from feishu_mcp.urls import UnsupportedDocError, parse_doc_ref


def test_table_structure_preserved():
    """结构化读取应将表格还原为 Markdown 表，而非连续文本。"""
    md = blocks_to_markdown(build(), "登录模块")
    assert "| 功能点 | 验收标准 |" in md
    assert "| 手机号登录 |" in md
    # 单元格里的 | 必须转义，否则会破坏表格列
    assert r"格式正确\|密码匹配" in md


def test_headings_lists_todos():
    md = blocks_to_markdown(build(), "t")
    assert "# 登录模块需求" in md
    assert "- 支持手机号登录" in md
    assert "1. 输入账号" in md and "2. 点击登录" in md
    assert "- [x] 已完成接口联调" in md
    assert "- [ ] 待补充异常提示文案" in md


def test_adjacent_lists_separated():
    """不同类型的相邻列表之间要有空行，否则 Markdown 会并成一个列表。"""
    md = blocks_to_markdown(build(), "t")
    lines = md.split("\n")
    bullet_end = max(i for i, l in enumerate(lines) if l.startswith("- 支持邮箱"))
    ordered_start = min(i for i, l in enumerate(lines) if l.startswith("1. "))
    assert lines[bullet_end + 1].strip() == ""
    assert ordered_start > bullet_end


def test_code_block_language():
    md = blocks_to_markdown(build(), "t")
    assert "```bash" in md
    assert 'curl -X POST /api/login' in md


def test_unreadable_blocks_reported_not_dropped():
    """bitable 读不到内容，但必须留痕说明，不能静默丢弃。"""
    md = blocks_to_markdown(build(), "t")
    assert "多维表格" in md and "bascnBASE456" in md
    assert "feishu-image:boxcnIMG123" in md


def test_summarize():
    stats = summarize_blocks(build())
    assert "table×1" in stats and "table_cell×6" in stats


def test_empty_doc():
    assert "文档为空" in blocks_to_markdown([], "空文档")


def test_unknown_block_type_keeps_text():
    """未收录的类型仍应保留可提取的文字。"""
    items = [
        {"block_id": "r", "block_type": 1, "children": ["x"]},
        {
            "block_id": "x",
            "block_type": 777,
            "parent_id": "r",
            "mystery": {"elements": [{"text_run": {"content": "重要内容", "text_element_style": {}}}]},
        },
    ]
    assert "重要内容" in blocks_to_markdown(items)


def test_table_without_column_size_degrades():
    """拿不到列信息时逐格输出，宁可格式退化也不丢内容。"""
    items = [
        {"block_id": "t", "block_type": 31, "children": ["c1"], "table": {"property": {}}},
        {"block_id": "c1", "block_type": 32, "parent_id": "t", "children": ["x1"]},
        {
            "block_id": "x1",
            "block_type": 2,
            "parent_id": "c1",
            "text": {"elements": [{"text_run": {"content": "孤立单元格", "text_element_style": {}}}]},
        },
    ]
    assert "孤立单元格" in blocks_to_markdown(items)


def test_url_parsing():
    assert parse_doc_ref("https://x.feishu.cn/docx/Abc123456789").kind == "docx"
    assert parse_doc_ref("https://x.feishu.cn/wiki/Wik123456789?f=1").is_wiki
    assert parse_doc_ref("wikcn1234567890").is_wiki
    assert parse_doc_ref("https://x.feishu.cn/mindnote/Mind123456789#mindmap").is_mindnote
    assert parse_doc_ref("https://x.feishu.cn/mindnotes/Mind123456789").is_mindnote
    for bad in ("https://x.feishu.cn/base/bascn123456", "https://x.feishu.cn/sheets/shtcn12345", "junk"):
        try:
            parse_doc_ref(bad)
            raise AssertionError(f"应当拒绝 {bad}")
        except UnsupportedDocError:
            pass


def test_split_never_breaks_tables_or_fences():
    parts = []
    for i in range(6):
        parts += [f"## 段 {i}", "", f"正文 {i}", ""]
        parts += ["| a | b |", "| --- | --- |"] + [f"| r{i} | v{j} |" for j in range(4)] + [""]
        parts += ["```python", "x=1", "", "y=2", "```", ""]
    src = "\n".join(parts)
    chunks = _split_markdown(src, max_blocks=10)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0, "代码围栏被切断"
        if any(l.startswith("|") for l in chunk.split("\n")):
            assert any(
                l.startswith("|") and set(l.replace("|", "").replace(" ", "")) <= {"-"}
                for l in chunk.split("\n")
            ), "表格分隔行被切掉"
    # 内容不能在切分中丢失
    assert "".join(c.replace("\n", "") for c in chunks) == src.replace("\n", "")


def test_title_normalization():
    assert normalize_title("# 我的  标题\n\n") == "我的 标题"
    assert normalize_title("") == "未命名文档"
    assert len(normalize_title("长" * 900)) == 800


def test_detect_images():
    assert detect_images("![a](x.png) 和 ![](y.jpg)") == ["x.png", "y.jpg"]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{'全部通过' if not failed else str(failed) + ' 个失败'}")
    raise SystemExit(1 if failed else 0)
