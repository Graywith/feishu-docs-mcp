# Feishu Docs MCP Server

基于飞书 OpenAPI 的本地 STDIO MCP Server，向 MCP 客户端提供云文档、知识库、文本附件和 MindNote 的结构化读写能力。服务使用飞书用户身份调用 API，可用于 Codex、Claude Code 及其他支持 STDIO 传输的 MCP 客户端。

## 核心能力

- 将飞书新版文档块还原为 Markdown，保留标题层级、列表、待办、代码块和表格结构。
- 通过 Wiki 节点解析底层文档类型和实体 token，统一处理知识库链接。
- 读取 Wiki 中的 Markdown 和纯文本附件，内容仅保留在进程内存中。
- 从 Markdown 创建飞书文档，或向现有文档首部、末尾追加内容。
- 读取 MindNote 节点树，并支持创建整篇 MindNote、创建子节点和更新节点。
- 支持用户授权持久化、access token 自动刷新和多进程刷新串行化。
- 对飞书应用级频控和文档并发编辑限制执行限速、退避和有界重试。

## MCP 工具

| 工具 | 作用 | 关键行为 |
|---|---|---|
| `feishu_read_doc` | 读取云文档、Wiki 文档或文本附件 | `structured` 模式保留块结构；`raw` 模式优先速度 |
| `feishu_get_doc_meta` | 读取标题和版本元数据 | 用于小成本确认文档身份 |
| `feishu_search_docs` | 搜索云文档和知识库节点 | 返回标题、类型和可访问链接 |
| `feishu_wiki_tree` | 遍历知识库节点 | 支持起始节点和深度限制 |
| `feishu_create_doc` | 创建新版飞书文档 | 将 Markdown 转换为飞书块并分片写入 |
| `feishu_append_doc` | 向现有文档写入 Markdown | 支持追加到末尾或插入到开头 |
| `feishu_read_mindnote` | 读取 MindNote | 还原父子关系，保留 `node_id`、备注、高亮和完成状态 |
| `feishu_create_mindnote` | 创建整篇 MindNote | 根据现有 Wiki 节点确定知识库与父目录，并初始化节点树 |
| `feishu_upsert_mindnote_node` | 创建或更新 MindNote 节点 | 新建时传 `parent_id`，更新时传 `node_id` |

## 运行模型

MCP 客户端通过 `run_server.py` 启动独立 STDIO 进程。服务从项目根目录加载 `.env`，从 `cache/token.json` 加载当前飞书用户的 OAuth token，然后通过统一 HTTP 客户端调用飞书 OpenAPI。

`FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 标识飞书应用；`cache/token.json` 标识实际授权的飞书用户。因此同一份服务代码可由多人分别部署，每人使用自己的应用凭证和用户授权结果。如果团队共用同一个飞书应用，应通过密钥管理系统分发 App Secret，每位使用者仍需单独完成 OAuth 授权。

## 部署

### 1. 环境要求

- Python 3.10 或更高版本。
- 可创建自建应用的飞书开放平台账号。
- Codex、Claude Code 或其他支持 STDIO MCP 的客户端。

`.runtime/` 是可选的本机 Python 运行时目录，与操作系统和 CPU 架构绑定，不属于源码发布物，因此与 `.venv/` 一样被 `.gitignore` 排除。仓库使用者应使用本机 Python 创建虚拟环境。

### 2. 安装依赖

Windows PowerShell：

```powershell
git clone <repository-url> feishu-docs-mcp
Set-Location feishu-docs-mcp
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux：

```bash
git clone <repository-url> feishu-docs-mcp
cd feishu-docs-mcp
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
```

### 3. 配置飞书应用

在[Feishu 开放平台](https://open.feishu.cn/app)创建企业自建应用，并开通以下用户身份权限，不过我建议直接开通应用和用户身份的全部云文档权限（），避免后面更新能力反复审批：

| 权限 | 用途 |
|---|---|
| `docx:document:readonly` | 读取新版文档元数据、原始文本和块结构 |
| `docx:document` | 创建文档、转换 Markdown 块、写入和追加正文 |
| `wiki:wiki:readonly` | 解析 Wiki 节点、读取知识库树 |
| `wiki:node:create` | 在知识库中创建整篇 MindNote |
| `drive:drive:readonly` | 搜索云文档、下载 Wiki 文本附件 |
| `mindnote:node:read` | 读取 MindNote 节点 |
| `mindnote:node:create` | 创建或更新 MindNote 节点 |
| `offline_access` | 获取 refresh token，支持长期授权 |

在应用的安全设置中添加 OAuth 重定向地址：

```text
http://localhost:3000/callback
```

如果需要改用其他端口，必须同时修改开放平台重定向地址和 `.env` 中的 `FEISHU_OAUTH_PORT`。权限配置完成后需发布应用版本；未发布的权限不会进入用户授权范围。

### 4. 配置本地凭证

复制 `.env.example` 为 `.env`：

```dotenv
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_OAUTH_PORT=3000
FEISHU_TENANT_DOMAIN=https://your-tenant.feishu.cn
```

`FEISHU_TENANT_DOMAIN` 用于生成可点击的文档链接，不参与 API 鉴权。`.env` 只应保存在部署机器，不应加入版本库。

### 5. 完成用户 OAuth 授权

Windows：

```powershell
& .\.venv\Scripts\python.exe .\scripts\login.py
```

macOS / Linux：

```bash
./.venv/bin/python ./scripts/login.py
```

脚本会打开飞书授权页，并在本机 OAuth 回调成功后将 token 写入 `cache/token.json`。服务会在 token 临近过期时自动刷新；多个 MCP 进程共享同一份 token 文件时，跨进程锁会防止一次性 refresh token 被并发使用。

### 6. 注册到 Codex

Codex 将用户级 MCP 配置保存在 `~/.codex/config.toml`，Codex 桌面端、CLI 和 IDE 扩展共享该配置。以 Windows PowerShell 为例：

```powershell
$McpRoot = (Resolve-Path .).Path
$McpPython = Join-Path $McpRoot ".venv\Scripts\python.exe"
$McpEntry = Join-Path $McpRoot "run_server.py"
codex mcp add feishu -- $McpPython $McpEntry
codex mcp get feishu --json
```

也可将 `integrations/codex-user.toml.example` 的配置合并到 `~/.codex/config.toml`，并将其中的占位路径替换为项目绝对路径。具体字段定义参见 [Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

macOS / Linux 使用 `.venv/bin/python` 作为命令，`run_server.py` 仍使用绝对路径。

### 7. 注册到 Claude Code

Claude Code 的 `user` scope 会使服务在当前系统用户的所有项目中可用。Windows PowerShell：

```powershell
$McpRoot = (Resolve-Path .).Path
$McpPython = Join-Path $McpRoot ".venv\Scripts\python.exe"
$McpEntry = Join-Path $McpRoot "run_server.py"
claude mcp add --scope user feishu -- $McpPython $McpEntry
claude mcp get feishu
```

macOS / Linux：

```bash
MCP_ROOT="$(pwd)"
claude mcp add --scope user feishu -- \
  "$MCP_ROOT/.venv/bin/python" "$MCP_ROOT/run_server.py"
claude mcp get feishu
```

`--` 用于分隔 Claude CLI 选项与 MCP 服务命令；`--scope user` 用于跨项目共享。如果通过 JSON 管理配置，可以使用 `integrations/claude-user.json.example` 作为配置片段，将 `command` 和 `args` 替换为部署机器上的绝对路径，再把 `mcpServers.feishu` 合并到 `~/.claude.json` 顶层。用户级配置不要放进项目的 `.mcp.json`：后者属于项目范围，需要随项目分发并由各项目分别确认。范围与命令规则参见 [Claude Code MCP 文档](https://docs.anthropic.com/en/docs/claude-code/mcp)。

Codex 和 Claude Code 都会在会话启动时加载 MCP 工具清单。新增或更改配置后，需重启客户端或重新打开会话。

## 项目结构

```text
feishu-docs-mcp/
├── feishu_mcp/
│   ├── auth.py             # OAuth、token 持久化与刷新锁
│   ├── client.py           # OpenAPI 请求、限速、重试和分页
│   ├── docs.py             # 文档读取与搜索编排
│   ├── wiki.py             # Wiki 节点解析与遍历
│   ├── mindnote.py         # MindNote 读写与树结构还原
│   ├── blocks_to_md.py     # 飞书文档块转 Markdown
│   ├── md_to_blocks.py     # Markdown 转飞书文档块
│   ├── attachments.py      # Wiki 文本附件读取
│   └── server.py           # MCP 工具定义
├── integrations/           # MCP 客户端用户级配置示例
├── scripts/                # 授权、协议检查与开发辅助脚本
├── tests/                  # 离线回归测试
├── requirements.txt        # 运行时依赖
├── pyproject.toml          # Python 包元数据
└── run_server.py           # 与工作目录无关的 STDIO 启动入口
```

## 能力边界

| 限制 | 原因与行为 |
|---|---|
| 不搬运 Markdown 图片 | 飞书 `blocks/convert` 不负责上传媒体。服务会跳过图片并返回未搬运数量，避免生成无效引用。 |
| 文本附件限于 `.md`、`.markdown`、`.txt` | 二进制附件需要独立格式解析器。未配置解析器时拒绝将 PDF、Word 或 Excel 当作文本解码。 |
| 单个文本附件最大 8 MiB | 附件直接下载到进程内存，大小限制用于约束单次 MCP 调用的内存占用。 |
| 不解析多维表格、电子表格和画板内容 | Docx API 在容器块中只返回关联 token，底层数据需要 Bitable、Sheets 或 Board 专用 API 及额外权限。输出会保留类型和 token，不会静默丢弃。 |
| 不支持旧版 `/docs/` 正文 | 旧版文档与 Docx 块模型不兼容，需先在飞书中升级为新版文档。 |
| MindNote 节点仅支持安全的纯文本写入 | 飞书节点模型还支持链接、用户和文档引用；当前工具仅生成可确定验证的文本、备注、高亮和完成状态。 |
| 不删除 MindNote 内部节点 | 飞书 MindNote OpenAPI 目前只提供节点读取与创建/更新接口，没有节点删除接口或对应权限。 |
| 不提供文档删除工具 | 删除不属于内容读写的必要链路，且在自动化场景中具有不可逆风险。 |
| OAuth 使用飞书 v2 token 端点 | 端点已在 `config.py` 集中定义，不与文档、Wiki 或 MindNote 业务逻辑耦合。 |

## 安全约定

- `.env`、`cache/`、`.venv/` 和 `.runtime/` 必须保持在版本库之外。
- 工具使用用户 access token，只能访问授权用户本身有权访问的文档。
- 附件下载仅保留在内存中；只有授权 token 和开发调试产物会写入 `cache/`。
- MCP 客户端配置中只保存启动命令，不应复制 App Secret 或用户 token。
