"""
案例库 MCP Server — 司法案例语义检索。
支持两种传输模式：
  stdio: python server.py                        （本地 MCP，默认）
  http:  python server.py --transport http        （远端部署，只读）
"""
# NO_PROXY 中的 [::1] 会导致 huggingface_hub → httpx URL 解析崩溃，必须在一切 import 前清除
import os as _os
_os.environ.pop("NO_PROXY", None)
_os.environ.pop("no_proxy", None)
# 国内环境 HuggingFace 不通，必须用镜像
if "HF_ENDPOINT" not in _os.environ:
    _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import os
import sys
import json
from typing import Optional

# 确保 server.py 所在目录在 sys.path 中（MyAgents 的 cwd 不是这里）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio

from embed import embed_single
from search import get_searcher

# --- 全局配置 ---
READONLY = False  # HTTP 模式下为 True，不暴露 sync_now

# --- MCP Server ---
server = Server("case-library")


def _build_table(results: list, with_similarity: bool = False) -> str:
    """将案例列表渲染为 Markdown 表格"""
    lines = []
    if with_similarity:
        lines.append("| # | 案例 | 来源 | 案号 | 法院 | 裁判观点 | 相似度 |")
        lines.append("|---|------|------|------|------|----------|--------|")
    else:
        lines.append("| # | 案例 | 来源 | 案号 | 法院 | 裁判观点 |")
        lines.append("|---|------|------|------|------|----------|")

    for i, c in enumerate(results, 1):
        local_id = c.get("local_id", i - 1)
        title = c.get("title", "").strip()[:40]
        source = c.get("source", "")[:8] or "-"
        ah = c.get("ah", "")[:24] or "-"
        court = (c.get("court") or "-")[:12]
        gist = (c.get("gist") or "-")[:80].replace("\n", " ").replace("|", "/")

        title_cell = title

        if with_similarity:
            sim = c.get("similarity", 0)
            lines.append(f"| {i} | {title_cell} | {source} | {ah} | {court} | {gist} | {sim:.2f} |")
        else:
            lines.append(f"| {i} | {title_cell} | {source} | {ah} | {court} | {gist} |")

    # 底部附上 local_id 对照和详情查看提示
    ids = ", ".join(f"#{r.get('local_id',i)}:{r.get('title','')[:15]}" for i, r in enumerate(results, 1))
    lines.append(f"\n详情: get_case_detail(local_id=N)")

    return "\n".join(lines)


def _fmt_case_detail(c: dict) -> str:
    """格式化单个案例详细信息"""
    parts = [
        f"# {c.get('label','')} — {c.get('title','')}",
        f" {c.get('cat','')} / {c.get('cause','')}  |   {c.get('court','')}  |  📅 {c.get('year','')}",
        f"\n## 关键词\n{c.get('keywords','')}",
        f"\n## 裁判要点\n{c.get('gist','')}",
    ]
    if c.get("issue"):
        parts.append(f"\n## 争议焦点\n{c.get('issue','')}")
    if c.get("full"):
        parts.append(f"\n## 全文\n{c.get('full','')[:5000]}")
    if c.get("statutes"):
        parts.append(f"\n## 相关法条\n{c.get('statutes','')}")
    if c.get("url"):
        parts.append(f"\n## 原文链接\n{c.get('url','')}")
    if c.get("ah"):
        parts.append(f"\n## 案号\n{c.get('ah','')}")
    if c.get("expert"):
        parts.append(f"\n## 专家点评\n{c.get('expert','')}")
    return "\n".join(parts)


@server.list_tools()
async def list_tools():
    tools = [
        Tool(
            name="search_similar_cases",
            description="语义检索类案。输入自然语言描述（如'小股东查账被拒'），返回最相似案例的裁判要点和相似度。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询，描述案件事实或法律问题"},
                    "top_k": {"type": "integer", "description": "返回结果数量，默认 10", "default": 10},
                    "cat": {"type": "string", "description": "案件类别过滤：民事/刑事/行政/执行/国家赔偿/调解"},
                    "source": {"type": "string", "description": "来源过滤：指导案例/公报案例/法答网/案例库案例"},
                    "year_min": {"type": "integer", "description": "最小年份"},
                    "year_max": {"type": "integer", "description": "最大年份"},
                    "court_like": {"type": "string", "description": "法院名称模糊匹配"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_case_detail",
            description="获取指定案例的完整信息（基本案情、裁判理由、全文等）",
            inputSchema={
                "type": "object",
                "properties": {
                    "local_id": {"type": "integer", "description": "案例本地ID（从 search_similar_cases 返回结果中获取）"},
                },
                "required": ["local_id"],
            },
        ),
        Tool(
            name="filter_cases",
            description="按条件精确过滤案例（不做语义搜索），支持法院、年份、案由、来源等维度。",
            inputSchema={
                "type": "object",
                "properties": {
                    "cat": {"type": "string"},
                    "source": {"type": "string"},
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                    "court": {"type": "string"},
                    "cause": {"type": "string"},
                    "limit": {"type": "integer", "description": "返回数量上限，默认 30", "default": 30},
                },
            },
        ),
        Tool(
            name="library_stats",
            description="查看案例库统计数据：总数、类别分布、数据源分布、年份范围。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]
    # HTTP 远端模式不暴露 sync_now（安全：数据更新由管理员在服务器上手动执行）
    if not READONLY:
        tools.append(Tool(
            name="sync_now",
            description="手动触发案例库增量同步（从 rmfyalk 拉取最新入库案例）。同步可能需要数分钟，完成后返回新增案例数。",
            inputSchema={
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean", "description": "仅检查不写入，默认 false", "default": False},
                    "source": {"type": "string", "description": "数据源：case_library（默认）或 all", "default": "case_library"},
                },
            },
        ))
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    searcher = get_searcher()

    if name == "search_similar_cases":
        query = arguments["query"]
        top_k = arguments.get("top_k", 10)
        filters = {}
        for k in ["cat", "source", "year_min", "year_max", "court_like"]:
            if k in arguments and arguments[k] is not None:
                filters[k] = arguments[k]

        # 1. embed query
        query_vec = embed_single(query)

        # 2. search（两阶段：embedding 粗筛 + reranker 精排）
        results = searcher.search(query_vec, top_k=top_k, filters=filters, query_text=query)

        if not results:
            return [TextContent(type="text", text="未找到匹配案例。请尝试调整查询或放宽过滤条件。")]

        output_parts = []
        output_parts.append(f" 搜索「{query}」—— 返回 {len(results)} 个结果\n")
        output_parts.append(_build_table(results, with_similarity=True))
        output_parts.append("")
        cats = set(r.get("cat", "") for r in results)
        sources = set(r.get("source", "") for r in results)
        output_parts.append(
            f" 覆盖 {len(cats)} 个类别 | {len(sources)} 个数据源 | "
            f"相似度 {results[-1]['similarity']:.4f} ~ {results[0]['similarity']:.4f}"
        )
        output_parts.append(
            f"\n 用 `get_case_detail(local_id=N)` 查看案例全文（N 为序号对应的 local_id）"
        )

        return [TextContent(type="text", text="\n".join(output_parts))]

    elif name == "get_case_detail":
        local_id = arguments["local_id"]
        case = searcher.get_case(local_id)
        if case is None:
            return [TextContent(type="text", text=f"案例 {local_id} 不存在。")]
        return [TextContent(type="text", text=_fmt_case_detail(case))]

    elif name == "filter_cases":
        filters = {}
        for k in ["cat", "source", "year_min", "year_max", "court", "cause"]:
            if k in arguments and arguments[k] is not None:
                filters[k] = arguments[k]
        limit = arguments.get("limit", 30)

        results = searcher.filter_only(filters, limit=limit)
        if not results:
            return [TextContent(type="text", text="未找到匹配案例。")]

        output_parts = [f" 过滤结果 — {len(results)} 个案例（最多显示 {limit} 个）\n"]
        output_parts.append(_build_table(results))
        return [TextContent(type="text", text="\n".join(output_parts))]

    elif name == "library_stats":
        stats = searcher.stats()
        parts = [
            f"#  案例库统计",
            f"总数：{stats['total']} 个案例",
            f"年份范围：{stats['year_range']}",
            f"\n## 类别分布",
        ]
        for cat, count in stats["by_category"].items():
            parts.append(f"  {cat}: {count}")
        parts.append(f"\n## 来源分布")
        for src, count in stats["by_source"].items():
            parts.append(f"  {src}: {count}")

        return [TextContent(type="text", text="\n".join(parts))]

    elif name == "sync_now":
        dry_run = arguments.get("dry_run", False)
        source = arguments.get("source", "case_library")
        try:
            import asyncio
            from sync import sync_all, sync_case_library
            if source == "case_library":
                result = await asyncio.to_thread(sync_case_library, dry_run=dry_run)
            else:
                result = await asyncio.to_thread(sync_all, dry_run=dry_run)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"同步失败: {e}")]

    return [TextContent(type="text", text=f"未知工具: {name}")]


def main():
    """MCP 入口。--transport stdio（默认）或 --transport http"""
    import argparse
    import asyncio

    p = argparse.ArgumentParser(description="法随案例库 MCP Server")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                   help="传输模式: stdio（本地）或 http（远端部署）")
    p.add_argument("--port", type=int, default=8765,
                   help="HTTP 模式端口（默认 8765）")
    p.add_argument("--host", type=str, default="0.0.0.0",
                   help="HTTP 模式监听地址（默认 0.0.0.0）")
    args = p.parse_args()

    if args.transport == "stdio":
        async def run():
            async with mcp.server.stdio.stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
        asyncio.run(run())

    else:
        global READONLY
        READONLY = True
        print(f"法随案例库 MCP HTTP Server → http://{args.host}:{args.port}/mcp")
        print(f"模式: 只读（sync_now 不暴露）")

        from starlette.applications import Starlette
        from starlette.routing import Route
        from mcp.server.sse import SseServerTransport

        sse = SseServerTransport("/mcp")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await server.run(
                    streams[0], streams[1],
                    server.create_initialization_options(),
                )

        async def health(request):
            from starlette.responses import JSONResponse
            return JSONResponse({"status": "ok"})

        app = Starlette(
            debug=False,
            routes=[
                Route("/health", health),
                Route("/mcp", handle_sse),
            ],
        )

        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
