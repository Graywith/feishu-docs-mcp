"""导出文档 blocks 响应，用于解析器开发与格式覆盖分析。

支持检查块类型、表格与单元格关系，以及 Markdown 转换接口的响应结构。

用法：
    .venv/Scripts/python.exe scripts/dump_doc.py <文档链接>
    .venv/Scripts/python.exe scripts/dump_doc.py <文档链接> --convert
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from feishu_mcp import config, docs  # noqa: E402
from feishu_mcp.blocks_to_md import (  # noqa: E402
    BLOCK_TYPE_NAMES,
    blocks_to_markdown,
    summarize_blocks,
)
from feishu_mcp.client import FeishuClient  # noqa: E402
from feishu_mcp.md_to_blocks import CONVERT_PATH  # noqa: E402

SAMPLE_MD = """# 标题一

一段正文，含 **粗体** 与 `行内代码`。

- 无序项 A
- 无序项 B

1. 有序项一
2. 有序项二

- [ ] 未完成待办
- [x] 已完成待办

| 功能点 | 验收标准 |
| --- | --- |
| 登录 | 密码正确则进首页 |
| 登出 | 清除会话 |

```python
print("hello")
```

> 引用一行
"""


async def dump_blocks(client: FeishuClient, target: str) -> None:
    document_id, ref = await docs.resolve_document_id(client, target)
    meta = await docs.get_meta(client, document_id)
    items = await docs.fetch_blocks(client, document_id)

    out_dir = config.CACHE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"blocks_{document_id}.json"
    raw_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_path = out_dir / f"blocks_{document_id}.md"
    markdown = blocks_to_markdown(items, meta.get("title") or "")
    md_path.write_text(markdown, encoding="utf-8")

    print(f"标题：{meta.get('title')}")
    print(f"document_id：{document_id}" + ("（由 wiki 节点解析）" if ref.is_wiki else ""))
    print(f"{summarize_blocks(items)}")
    print(f"原始 JSON -> {raw_path}")
    print(f"解析结果 -> {md_path}")

    # 汇总当前解析器尚未显式收录的块类型
    unknown = sorted(
        {
            int(i.get("block_type") or 0)
            for i in items
            if int(i.get("block_type") or 0) not in BLOCK_TYPE_NAMES
        }
    )
    if unknown:
        print(f"发现未收录的 block_type：{unknown}（走了兜底渲染，可按需补进 BLOCK_TYPE_NAMES）")

    # 核对表格结构：确认 column_size 与 TableCell 数量是否自洽
    tables = [i for i in items if int(i.get("block_type") or 0) == 31]
    for tbl in tables:
        prop = (tbl.get("table") or {}).get("property") or {}
        cells = len(tbl.get("children") or [])
        print(
            f"表格 {tbl.get('block_id')}: property={prop} 单元格数={cells} "
            f"（行×列应等于单元格数）"
        )

    # 输出 text 和 table_cell 样本，便于分析字段结构
    for want, label in ((2, "text 块"), (32, "table_cell 块")):
        sample = next((i for i in items if int(i.get("block_type") or 0) == want), None)
        if sample:
            print(f"\n--- {label}原始结构 ---")
            print(json.dumps(sample, ensure_ascii=False, indent=2)[:900])


async def dump_convert(client: FeishuClient) -> None:
    """导出 Markdown 转换接口的响应结构。"""
    data = await client.post(CONVERT_PATH, {"content_type": "markdown", "content": SAMPLE_MD})
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CACHE_DIR / "convert_sample.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"convert 响应顶层字段：{sorted(data.keys())}")
    print(f"first_level_block_ids 数量：{len(data.get('first_level_block_ids') or [])}")
    for key in ("blocks", "block_id_to_block"):
        if key in data:
            value = data[key]
            print(f"块结构字段名是 `{key}`，类型 {type(value).__name__}，元素数 {len(value)}")
    print(f"完整响应 -> {path}")


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_convert = "--convert" in sys.argv

    client = FeishuClient()
    try:
        if want_convert:
            await dump_convert(client)
        if args:
            await dump_blocks(client, args[0])
        elif not want_convert:
            print(__doc__)
            return 1
        return 0
    except Exception as exc:
        print(f"失败：{exc}")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
