# 法随·指导案例MCP

基于最高人民法院案例库的语义检索 MCP 工具，支持自然语言查找类案。

## 案例类型

共收录 **7,364 条**权威案例，覆盖 7 类：

| 案例类型 | 数量 | 效力 | 说明 |
|----------|------|------|------|
| 案例库案例 | 5,205 | 入库参考 | 最高法统一筛选入库，法官办案检索参考 |
| 指导案例 | 558 | 应当参照 | 最高法审委会讨论通过 |
| 典型案例 | 544 | 示范意义 | 最高法/最高检专题发布 |
| 公报案例 | 457 | 可以参考 | 最高法公报发布 |
| 最高检指导性案例 | 239 | 应当参照 | 最高检检委会讨论通过 |
| 最高检典型案例 | 196 | 示范意义 | 最高检专题发布 |
| 法答网 | 165 | 精选问答 | 最高法研究室权威答疑 |

数据每周增量更新（案例库案例自动同步），无需手动维护。

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

### 方式二：独立同步模式

```bash
git clone https://github.com/alexchenlin1996-pixel/aluris-caselibrary-mcp.git
cd aluris-caselibrary-mcp
uv sync
python setup_wizard.py --choice 2   # 引导配置 API Key + 登录，自行接管增量更新
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

## 接入 MCP 客户端

标准 MCP stdio 协议，任意客户端只需配置三个要素：

| 要素 | 值 |
|------|-----|
| 命令 | `uv --directory /path/to/aluris-caselibrary-mcp run python server.py` |
| 环境变量 | `ZHIPU_API_KEY` + `CASE_DB_PATH` |

各客户端语法不同但本质相同，举例如下：

**MyAgents** — 设置页 MCP 服务器中添加 stdio 类型，command 填 `uv`，args 填 `--directory` `/path/to/aluris-caselibrary-mcp` `run` `python` `server.py`

**Claude Code / Gemini CLI** — `mcp add` 命令：

```bash
claude mcp add case-library \
  -e ZHIPU_API_KEY=your-key -e CASE_DB_PATH=~/.myagents/case_db \
  -- uv --directory /path/to/aluris-caselibrary-mcp run python server.py
```

**Codex** — `codex.yaml` 的 `mcp_servers` 下按 command/args/env 配置即可

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
- httpx (公开数据同步)

## License

MIT
