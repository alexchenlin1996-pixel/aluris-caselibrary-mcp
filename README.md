# 法随案例库 MCP

基于最高人民法院案例库的语义检索 MCP 工具，支持自然语言查找类案。

## 数据来源

- 人民法院案例库 (rmfyalk.court.gov.cn) — 5428 篇，每周增量更新
- 最高人民法院指导案例
- 最高人民法院公报案例
- 法答网精选问答

## MCP 工具

| 工具 | 说明 |
|------|------|
| `search_similar_cases` | 语义检索类案，输入案情描述返回最相似案例 |
| `get_case_detail` | 查看案例全文（裁判要点、基本案情、法条等） |
| `filter_cases` | 按法院、年份、案由、来源精确过滤 |
| `library_stats` | 案例库统计概览 |
| `sync_now` | 手动触发增量同步 |

## 安装

### 前置要求

- Python 3.11+
- uv（推荐，用于依赖管理）

### 方式一：GitHub Release 数据包（推荐）

```bash
git clone https://github.com/alexchenlin1996-pixel/aluris-caselibrary-mcp.git
cd aluris-caselibrary-mcp
uv sync
python setup_wizard.py --choice 1   # 自动下载数据包
```

### 方式二：独立爬取

```bash
git clone https://github.com/alexchenlin1996-pixel/aluris-caselibrary-mcp.git
cd aluris-caselibrary-mcp
uv sync
python setup_wizard.py --choice 2   # 引导配置 API Key + 登录
```

### 方式三：混合模式

```bash
python setup_wizard.py --choice 3   # 先拉包，后续自己接管增量
```

## 配置环境变量

| 变量 | 说明 |
|------|------|
| `ZHIPU_API_KEY` | 智谱 API Key（用于 embedding） |
| `CASE_DB_PATH` | 数据目录，默认 `~/.myagents/case_db` |

## 接入 MyAgents

在 MyAgents 设置中添加 MCP 服务器：

```json
{
  "id": "case-library",
  "name": "法随案例库",
  "type": "stdio",
  "command": "uv",
  "args": ["--directory", "/path/to/aluris-caselibrary-mcp", "run", "server.py"],
  "env": {
    "ZHIPU_API_KEY": "your-key",
    "CASE_DB_PATH": "/path/to/data"
  }
}
```

## 定时更新

```bash
# 手动执行
uv run python sync.py

# 或在 MyAgents 中设置定时任务，调用 sync_now 工具
```

## 技术栈

- 智谱 embedding-3 (2048维)
- numpy 余弦相似度
- FastMCP (Python MCP SDK)
- Playwright (rmfyalk 登录态)
- httpx (公开源抓取)

## License

MIT
