# 法随·案例库 MCP

AI 语义类案检索工具，7,372 条最高法 / 最高检权威案例，自然语言搜索。

## 案例覆盖

| 类型 | 数量 | 效力 |
|------|------|------|
| 案例库案例 | 5,211 | 入库参考 |
| 指导案例 | 556 | 应当参照 |
| 典型案例 | 544 | 示范意义 |
| 公报案例 | 461 | 可以参考 |
| 最高检指导性案例 | 239 | 应当参照 |
| 最高检典型案例 | 196 | 示范意义 |
| 法答网 | 165 | 精选问答 |
| **合计** | **7,372** | |

---

## 快速配置（远端模式，推荐）

服务地址：`https://aluris.top/mcp`

无需安装 Python，无需下载数据，直接在 AI 客户端里填入以下配置即可。

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
| `search_similar_cases` | 语义检索类案——自然语言描述案件事实，返回最相关案例 |
| `get_case_detail` | 获取案例完整信息（案情、裁判要点、全文、法条） |
| `filter_cases` | 按法院、年份、案由、来源精确过滤 |
| `library_stats` | 查看案例库统计数据 |

**示例提问：**
- "帮我找小股东被拒绝查阅会计账簿的案例"
- "搜索建设工程优先受偿权的指导案例"
- "有没有涉及格式条款无效的公报案例"

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
