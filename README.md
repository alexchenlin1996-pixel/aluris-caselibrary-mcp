# 法随·案例库 MCP

面向法律实务的权威案例与司法规则 MCP。聚焦最高法指导案例、公报案例、典型案例、案例库案例，同时纳入最高检指导性案例、最高检典型案例和法答网问答，方便法律人在办案、文书写作和类案检索中快速找到可引用、可核验、低噪音的权威依据。

大型法律数据库解决“查得全”，法随案例库 MCP 解决“AI 办案时优先查到权威案例和司法规则”。

## 案例覆盖

| 类型 | 数量 | 引用价值 |
|------|------|------|
| 案例库案例 | 5,211 | 重要参考 |
| 指导案例 | 556 | 应优先引用 |
| 典型案例 | 544 | 重要参考 |
| 公报案例 | 461 | 权威参考 |
| 最高检指导性案例 | 239 | 重要参考 |
| 最高检典型案例 | 196 | 参考 |
| 法答网 | 165 | 辅助参考 |
| **合计** | **7,372** | |

---

## 适用场景

- 办案时检索最高法、最高检权威案例和司法规则
- 起诉状、答辩状、代理意见中的裁判规则论证
- 类案检索报告中的案例筛选和引用摘要
- 法律适用口径、司法问答和实务规则核验
- AI 法律助手需要调用低噪音案例来源时

## 快速配置（远端模式，推荐）

推荐地址：`https://aluris.top/mcp`

该地址同时兼容 Streamable HTTP 和 SSE。

无需安装 Python，无需下载数据，直接在 AI 客户端里填入以下配置即可。WorkBuddy、Claude、Cursor 等使用“URL / HTTP MCP”配置的客户端，直接填写推荐地址；如果客户端要求选择类型，优先选择 Streamable HTTP / HTTP MCP。

---

### WorkBuddy（推荐）

远端 MCP 地址：

```
URL: https://aluris.top/mcp
类型: Streamable HTTP / HTTP MCP
```

已在 WorkBuddy v4.22.16 实测通过。推荐配置路径：

1. 打开 WorkBuddy 左侧「连接器」
2. 点击右上角「自定义连接器」
3. 进入「MCP 服务管理」后点击「配置 MCP」
4. 在 `~/.workbuddy/mcp.json` 的 `mcpServers` 中加入：

```json
{
  "mcpServers": {
    "fasui-caselibrary": {
      "type": "http",
      "url": "https://aluris.top/mcp",
      "description": "法随案例库：最高法、最高检、法答网权威案例检索，结果包含裁判规则、引用摘要和原文链接。",
      "disabled": false
    }
  }
}
```

5. 返回 MCP 列表，点击「信任」；正常状态会显示 `fasui-caselibrary 7/7 个工具已启用`

不要填写 `https://aluris.top/mcp/http`。如果在普通聊天框里直接粘贴 URL，WorkBuddy 可能会把它当作网页抓取；请通过「连接器 / 自定义连接器」入口配置 MCP。

如果 WorkBuddy 的配置项只有 SSE 类型，仍使用同一个地址：

```
URL: https://aluris.top/mcp
类型: SSE
```

---

### Claude Desktop

配置文件路径：
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "法随案例库": {
      "url": "https://aluris.top/mcp"
    }
  }
}
```

> 如果文件里已有其他 MCP 服务，在 `mcpServers` 对象里追加一个键即可：
> ```json
> {
>   "mcpServers": {
>     "已有的服务": { "...": "..." },
>     "法随案例库": {
>       "url": "https://aluris.top/mcp"
>     }
>   }
> }
> ```

修改完重启 Claude Desktop 生效。

---

### Cursor

配置文件路径：`~/.cursor/mcp.json`（全局）或项目根目录 `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "法随案例库": {
      "url": "https://aluris.top/mcp"
    }
  }
}
```

---

### Windsurf

配置文件路径：`~/.codeium/windsurf/mcp_config.json`

```json
{
  "mcpServers": {
    "法随案例库": {
      "url": "https://aluris.top/mcp"
    }
  }
}
```

---

### MyAgents / 其他支持 SSE 的 MCP 客户端

在 MCP 服务器配置里填入：

```
URL: https://aluris.top/mcp
类型: SSE (HTTP)
```

---

## MCP 工具说明

配置完成后，AI 可使用以下工具：

| 工具 | 用途 |
|------|------|
| `search_authoritative_cases` | 检索权威案例与司法规则，优先返回高引用价值来源 |
| `search_similar_cases` | 语义检索类案，兼容旧客户端调用 |
| `get_case_detail` | 按案例编号展开完整信息、权威类型、引用价值、适用场景 |
| `find_rules_by_issue` | 按法律争点查裁判规则 |
| `generate_case_citation_brief` | 按案例编号生成可放进检索报告或代理意见的案例引用摘要 |
| `filter_cases` | 按法院、年份、案由、来源精确过滤 |
| `library_stats` | 查看案例库统计数据 |

检索结果默认以卡片方式返回，每条卡片直接包含详情摘要、裁判规则、引用摘要和原文链接。案例编号主要用于继续展开全文或重新整理格式；需要最终汇总清单时，可以再要求整理为表格。

**示例提问：**
- “帮我找小股东被拒绝查阅会计账簿的案例”
- “搜索建设工程优先受偿权的指导案例”
- “有没有涉及格式条款无效的公报案例”
- “查一下最高检关于公益诉讼惩罚性赔偿的指导性案例”
- “法答网有没有关于执行异议之诉的答复口径”
- “给我生成这个案例的代理意见引用摘要”

---

## 权威排序

检索结果会综合考虑语义相似度、来源权威、新近程度和来源匹配。默认权重如下：

| 来源 | 权重 | 引用提示 |
|------|------|----------|
| 指导案例 | 1.00 | 应优先引用 |
| 公报案例 | 0.94 | 权威参考 |
| 典型案例 | 0.88 | 重要参考 |
| 最高检指导性案例 | 0.86 | 重要参考 |
| 案例库案例 | 0.82 | 重要参考 |
| 最高检典型案例 | 0.78 | 参考 |
| 法答网 | 0.70 | 辅助参考 |

## 引用安全

案例库会把检索和引用分开处理。语义检索可以返回相关案例，引用摘要只基于原始字段或原文中已经存在的规则段落生成，不根据标题、案情或相似案例补写规则。

规则质量分为：

| 等级 | 含义 | 用途 |
|------|------|------|
| A | 裁判要点、裁判摘要、执行实施要点、法答网答复等明确规则文本 | 可生成引用摘要 |
| B | 典型意义、案例分析、法院认为、检察监督意见等规则线索 | 可生成引用摘要，但提示核验 |
| D | 未提取到可引用规则段落 | 只作为案例线索，不生成引用摘要 |

`search_authoritative_cases` 和 `find_rules_by_issue` 默认只返回 A/B 级结果。`generate_case_citation_brief` 遇到 D 级案例会拒绝生成摘要，并提示打开原文核验。

---

## 本地部署（可选）

如需本地运行（数据存本地、支持增量同步），参考以下步骤：

```bash
git clone https://github.com/alexchenlin1996-pixel/aluris-caselibrary-mcp.git
cd aluris-caselibrary-mcp
uv sync
uv run python sync.py          # 首次拉取案例数据（需要时间）
```

本地 stdio 模式（Claude Desktop）：

```json
{
  "mcpServers": {
    "法随案例库-本地": {
      "command": "uv",
      "args": ["run", "python", "/path/to/aluris-caselibrary-mcp/server.py"],
      "env": {
        "CASE_DB_PATH": "/path/to/case_db"
      }
    }
  }
}
```

本地 HTTP 模式：

```bash
uv run python server.py --transport http --port 8765
```

**无外部 API 依赖**——embedding 使用本地 BGE 模型，首次运行自动下载（约 100MB）。

---

## 目录结构

```
├── server.py           # MCP 入口，支持 stdio / HTTP 双模式
├── search.py           # 两阶段检索（embedding + reranker）
├── embed.py            # fastembed + BAAI/bge-small-zh-v1.5
├── sync.py             # 增量同步协调器
└── sources/
    ├── case_library.py # 最高院案例库（rmfyalk，需登录）
    ├── guide_case.py   # 指导案例（公开）
    └── public_sources.py # 公报案例 + 法答网（公开）
```
