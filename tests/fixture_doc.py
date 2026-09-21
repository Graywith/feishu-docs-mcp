"""构造一份覆盖常见块类型的假 blocks 响应，用于离线验证解析器。

真实文档 JSON 结构以运行时 dump 为准（scripts/dump_doc.py），此夹具只保证
解析器在预期结构下行为正确。
"""


def _text(bid, content, parent, btype=2, style=None, children=None):
    return {
        "block_id": bid,
        "block_type": btype,
        "parent_id": parent,
        "children": children or [],
        SPEC[btype]: {
            "elements": [{"text_run": {"content": content, "text_element_style": {}}}],
            **({"style": style} if style else {}),
        },
    }


SPEC = {
    2: "text", 3: "heading1", 4: "heading2", 12: "bullet",
    13: "ordered", 14: "code", 15: "quote", 17: "todo",
}


def build():
    blocks = [
        {"block_id": "doc1", "block_type": 1, "children": [
            "h1", "p1", "b1", "b2", "o1", "o2", "todo1", "todo2",
            "tbl", "code1", "q1", "div1", "img1", "bt1", "callout1",
        ]},
        _text("h1", "登录模块需求", "doc1", 3),
        _text("p1", "本模块覆盖手机号与邮箱两种登录方式。", "doc1", 2),
        _text("b1", "支持手机号登录", "doc1", 12),
        _text("b2", "支持邮箱登录", "doc1", 12),
        _text("o1", "输入账号", "doc1", 13),
        _text("o2", "点击登录", "doc1", 13),
        _text("todo1", "已完成接口联调", "doc1", 17, style={"done": True}),
        _text("todo2", "待补充异常提示文案", "doc1", 17, style={"done": False}),
        {"block_id": "tbl", "block_type": 31, "parent_id": "doc1",
         "children": ["c1", "c2", "c3", "c4", "c5", "c6"],
         "table": {"property": {"row_size": 3, "column_size": 2, "header_row": True}}},
        _text("code1", "curl -X POST /api/login", "doc1", 14, style={"language": 7}),
        _text("q1", "需求评审已通过", "doc1", 15),
        {"block_id": "div1", "block_type": 22, "parent_id": "doc1", "divider": {}},
        {"block_id": "img1", "block_type": 27, "parent_id": "doc1",
         "image": {"token": "boxcnIMG123", "width": 100, "height": 50}},
        {"block_id": "bt1", "block_type": 18, "parent_id": "doc1",
         "bitable": {"token": "bascnBASE456"}},
        {"block_id": "callout1", "block_type": 19, "parent_id": "doc1",
         "children": ["ct1"], "callout": {"background_color": 1}},
        _text("ct1", "上线前需回归支付链路", "callout1", 2),
    ]
    cells = [
        ("c1", "功能点"), ("c2", "验收标准"),
        ("c3", "手机号登录"), ("c4", "格式正确|密码匹配 则跳转首页"),
        ("c5", "错误提示"), ("c6", "密码错误提示「账号或密码错误」"),
    ]
    for cid, val in cells:
        blocks.append({"block_id": cid, "block_type": 32, "parent_id": "tbl",
                       "children": [f"x{cid}"]})
        blocks.append(_text(f"x{cid}", val, cid, 2))
    return blocks
