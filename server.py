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


def _is_citable_result(case: dict) -> bool:
    return bool((case.get("rule_text") or "").strip()) and case.get("rule_quality") in ("A", "B")


def _build_table(results: list, with_similarity: bool = False) -> str:
    """将案例列表渲染为 Markdown 表格"""
    lines = []
    if with_similarity:
        lines.append("| # | 案例 | 来源 | 权威类型 | 引用价值/质量 | 案号 | 裁判观点 | 原文 | 综合分 |")
        lines.append("|---|------|------|----------|----------|------|----------|------|--------|")
    else:
        lines.append("| # | 案例 | 来源 | 权威类型 | 引用价值/质量 | 案号 | 裁判观点 | 原文 |")
        lines.append("|---|------|------|----------|----------|------|----------|------|")

    for i, c in enumerate(results, 1):
        local_id = c.get("local_id", i - 1)
        title = c.get("title", "").strip()[:40]
        source = c.get("source", "")[:8] or "-"
        ah = c.get("ah", "")[:24] or "-"
        authority_type = (c.get("authority_type") or "-")[:12]
        citation_value = (c.get("citation_value") or "-")[:10]
        rule_quality = c.get("rule_quality") or "-"
        rule = (
            c.get("rule_text")
            or c.get("gist")
            or "未提取到规则段落，请查看全文核验"
        )[:80].replace("\n", " ").replace("|", "/")
        source_link = _source_link(c)

        title_cell = title

        if with_similarity:
            score = c.get("combined_score", c.get("similarity", 0))
            lines.append(
                f"| {i} | {title_cell} | {source} | {authority_type} | "
                f"{citation_value}/{rule_quality} | {ah} | {rule} | {source_link} | {score:.2f} |"
            )
        else:
            lines.append(
                f"| {i} | {title_cell} | {source} | {authority_type} | "
                f"{citation_value}/{rule_quality} | {ah} | {rule} | {source_link} |"
            )

    lines.append("\n如需展开某条案例，可以直接说“查看第 N 条案例详情”或“把第 N 条整理成引用摘要”。")

    return "\n".join(lines)


def _build_cards(results: list, with_score: bool = False) -> str:
    """将检索结果渲染为便于办案判断的卡片。"""
    cards = []
    for i, c in enumerate(results, 1):
        rule = (
            c.get("rule_text")
            or c.get("gist")
            or "未提取到规则段落，请查看全文核验。"
        ).strip()
        if len(rule) > 600:
            rule = rule[:600] + "..."

        meta = [
            f"来源：{c.get('source') or '-'}",
            f"权威类型：{c.get('authority_type') or '-'}",
            f"引用价值：{c.get('citation_value') or '-'}",
            f"规则质量：{c.get('rule_quality') or '-'}",
        ]
        if c.get("ah"):
            meta.append(f"案号：{c.get('ah')}")
        if c.get("year"):
            meta.append(f"年份：{c.get('year')}")
        if with_score:
            meta.append(f"综合分：{c.get('combined_score', c.get('similarity', 0)):.4f}")

        cards.extend([
            f"## {i}. {c.get('title') or '-'}",
            "  |  ".join(meta),
            f"适用场景：{c.get('practice_scene') or '-'}",
            f"规则来源：{c.get('rule_source') or '未提取到可引用规则段落'}",
            f"案例详情摘要：{_case_detail_summary(c)}",
            f"\n裁判规则：{rule}",
            f"\n引用摘要：{_inline_citation_summary(c)}",
            f"\n原文链接：{_source_link(c)}",
            f"案例编号：{c.get('local_id')}（供继续展开详情或重新整理时使用）",
        ])
    return "\n\n".join(cards)


def _source_link(c: dict) -> str:
    url = c.get("url") or c.get("local_file") or ""
    if not url:
        return "-"
    if url.startswith("http://") or url.startswith("https://"):
        return f"[原文]({url})"
    return url


def _clip_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _case_detail_summary(c: dict) -> str:
    """生成检索卡片里的轻量详情摘要，避免用户再追问编号含义。"""
    fields = []
    if c.get("cat"):
        fields.append(f"类别 {c.get('cat')}")
    if c.get("cause"):
        fields.append(f"案由 {c.get('cause')}")
    if c.get("court"):
        fields.append(f"法院 {c.get('court')}")
    if c.get("year"):
        fields.append(f"年份 {c.get('year')}")
    if c.get("ah"):
        fields.append(f"案号 {c.get('ah')}")
    if c.get("keywords"):
        fields.append(f"关键词 {_clip_text(c.get('keywords'), 80)}")
    if c.get("statutes"):
        fields.append(f"相关法条 {_clip_text(c.get('statutes'), 100)}")
    if len(fields) < 3:
        source_path = _source_path_summary(c)
        if source_path:
            fields.append(f"来源位置 {source_path}")
    return "；".join(fields) if fields else "暂无结构化详情，建议打开原文核验。"


def _source_path_summary(c: dict) -> str:
    path = c.get("local_file") or c.get("url") or ""
    if path.startswith("file://"):
        path = path[7:]
    if not path or path.startswith("http://") or path.startswith("https://"):
        return ""
    parts = [p for p in path.split("/") if p]
    if "最高法案例MCP" in parts:
        parts = parts[parts.index("最高法案例MCP") + 1:]
    if len(parts) > 1:
        parts = parts[:-1]
    cleaned = []
    for part in parts[-3:]:
        cleaned.append(part.strip())
    return " / ".join(cleaned)


def _inline_citation_summary(c: dict) -> str:
    """生成卡片内可直接阅读的引用摘要，只使用已提取规则，不补写规则。"""
    rule_text = (c.get("rule_text") or "").strip()
    if not rule_text or c.get("rule_quality") not in ("A", "B"):
        reason = c.get("rule_quality_note") or "未提取到可引用规则段落"
        return f"该案例未达到可引用规则标准，不建议直接引用；原因：{reason}。请打开原文核验后再使用。"

    source = c.get("source") or "权威案例"
    title = c.get("title") or "该案例"
    rule_source = c.get("rule_source") or "规则段落"
    rule = _clip_text(rule_text, 260)
    caution = "引用前仍应结合原文、案由、事实细节和现行法律依据核验。"
    if c.get("source") == "法答网":
        caution = "法答网适合作为司法问答和法律适用口径的辅助参考，正式引用时建议同时核验规范依据。"
    elif str(c.get("source", "")).startswith("最高检"):
        caution = "最高检案例适合检察履职、刑事、公益诉讼、检察监督等场景，跨领域引用时需说明适用边界。"

    return (
        f"可引用方向：{source}《{title}》在{rule_source}中形成以下规则：{rule} "
        f"{caution}"
    )


def _fmt_case_detail(c: dict) -> str:
    """格式化单个案例详细信息"""
    rule_text = c.get("rule_text") or ""
    rule_source = c.get("rule_source") or ""
    parts = [
        f"# {c.get('label','')} — {c.get('title','')}",
        f"{c.get('cat','')} / {c.get('cause','')}  |  {c.get('court','')}  |  {c.get('year','')}",
        (
            f"权威类型：{c.get('authority_type','')}  |  "
            f"引用价值：{c.get('citation_value','')}  |  "
            f"规则质量：{c.get('rule_quality','')}  |  "
            f"适用场景：{c.get('practice_scene','')}"
        ),
        f"\n## 关键词\n{c.get('keywords','')}",
        f"\n## 裁判规则\n来源：{rule_source or '未提取到可引用规则段落'}\n\n{rule_text or '该案例缺少可直接引用的裁判规则段落，请查看全文核验。'}",
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


def _fmt_rule_results(issue: str, results: list) -> str:
    """按争点输出裁判规则线索。"""
    parts = [f"# 争点规则检索：{issue}"]
    for i, c in enumerate(results, 1):
        parts.extend([
            f"\n## {i}. {c.get('title','')}",
            f"来源：{c.get('source','')}  |  权威类型：{c.get('authority_type','')}  |  引用价值：{c.get('citation_value','')}",
            f"规则质量：{c.get('rule_quality','')}  |  适用场景：{c.get('practice_scene','')}",
            f"规则来源：{c.get('rule_source') or '-'}",
            f"裁判规则：{(c.get('rule_text') or '-')[:800]}",
            f"案例详情摘要：{_case_detail_summary(c)}",
            f"引用摘要：{_inline_citation_summary(c)}",
            f"原文链接：{_source_link(c)}",
            f"案例编号：{c.get('local_id')}（供继续展开详情或重新整理时使用）",
        ])
    return "\n".join(parts)


def _fmt_case_leads(title: str, results: list) -> str:
    """输出不可直接引用但值得人工核验的候选线索。"""
    parts = [
        f"# {title}",
        "未找到 A/B 级可引用规则结果，以下仅作为案例线索；引用前需要打开原文核验。",
    ]
    for i, c in enumerate(results, 1):
        parts.extend([
            f"\n## {i}. {c.get('title','')}",
            f"来源：{c.get('source','')}  |  权威类型：{c.get('authority_type','')}  |  规则质量：{c.get('rule_quality','D')}",
            f"原因：{c.get('rule_quality_note') or '未提取到可引用规则段落'}",
            f"摘要线索：{(c.get('rule_text') or c.get('gist') or c.get('full') or '-')[:500]}",
            f"案例详情摘要：{_case_detail_summary(c)}",
            f"原文链接：{_source_link(c)}",
            f"案例编号：{c.get('local_id')}（仅用于继续展开原文线索）",
            "使用提示：这类结果不能直接生成引用摘要，建议先打开原文核验。",
        ])
    return "\n".join(parts)


def _fmt_citation_brief(c: dict) -> str:
    """生成可放入检索报告或代理意见的案例摘要。"""
    rule_text = (c.get("rule_text") or "").strip()
    if not rule_text or c.get("rule_quality") not in ("A", "B"):
        return "\n".join([
            f"# 案例引用摘要：{c.get('title','')}",
            "该案例未达到可引用规则标准，不建议生成引用摘要。",
            "请先打开原文核验裁判要旨、裁判理由或典型意义后再引用。",
            f"规则质量：{c.get('rule_quality') or 'D'}",
            f"原因：{c.get('rule_quality_note') or '未提取到可引用规则段落'}",
            f"原文链接：{c.get('url','') or '-'}",
        ])
    if len(rule_text) > 1200:
        rule_text = rule_text[:1200] + "..."

    caution = "引用前应结合原文、案由、事实细节和现行法律依据核验。"
    if c.get("source") == "法答网":
        caution = "法答网适合说明司法问答和法律适用口径，正式引用时宜作为辅助参考。"
    elif str(c.get("source", "")).startswith("最高检"):
        caution = "最高检案例适合刑事、公益诉讼、检察监督等场景，民商事裁判论证中宜说明适用边界。"

    return "\n".join([
        f"# 案例引用摘要：{c.get('title','')}",
        f"来源：{c.get('source','')}",
        f"权威类型：{c.get('authority_type','')}",
        f"引用价值：{c.get('citation_value','')}",
        f"适用场景：{c.get('practice_scene','')}",
        f"规则质量：{c.get('rule_quality','')}",
        f"案号：{c.get('ah','') or '-'}",
        f"年份：{c.get('year','') or '-'}",
        f"规则来源：{c.get('rule_source') or '-'}",
        f"\n## 可引用裁判规则\n{rule_text}",
        f"\n## 引用提示\n{caution}",
        f"\n## 原文链接\n{c.get('url','') or '-'}",
    ])


@server.list_tools()
async def list_tools():
    tools = [
        Tool(
            name="search_authoritative_cases",
            description="检索权威案例与司法规则。优先返回最高法、最高检、法答网等权威来源，并标注权威类型、引用价值和适用场景。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询，描述案件事实、法律争点或希望查找的裁判规则"},
                    "top_k": {"type": "integer", "description": "返回结果数量，默认 8", "default": 8},
                    "cat": {"type": "string", "description": "案件类别过滤：民事/刑事/行政/执行/国家赔偿/调解"},
                    "source": {"type": "string", "description": "来源过滤：指导案例/公报案例/典型案例/案例库案例/最高检指导性案例/最高检典型案例/法答网"},
                    "year_min": {"type": "integer", "description": "最小年份"},
                    "year_max": {"type": "integer", "description": "最大年份"},
                    "court_like": {"type": "string", "description": "法院名称模糊匹配"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_similar_cases",
            description="语义检索类案。输入自然语言描述（如'小股东查账被拒'），返回相关案例，并标注权威类型和引用价值。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询，描述案件事实或法律问题"},
                    "top_k": {"type": "integer", "description": "返回结果数量，默认 10", "default": 10},
                    "cat": {"type": "string", "description": "案件类别过滤：民事/刑事/行政/执行/国家赔偿/调解"},
                    "source": {"type": "string", "description": "来源过滤：指导案例/公报案例/典型案例/案例库案例/最高检指导性案例/最高检典型案例/法答网"},
                    "year_min": {"type": "integer", "description": "最小年份"},
                    "year_max": {"type": "integer", "description": "最大年份"},
                    "court_like": {"type": "string", "description": "法院名称模糊匹配"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_case_detail",
            description="按搜索结果中的案例编号获取完整信息（基本案情、裁判理由、全文等）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "local_id": {"type": "integer", "description": "案例编号（搜索结果卡片底部显示的编号）"},
                },
                "required": ["local_id"],
            },
        ),
        Tool(
            name="find_rules_by_issue",
            description="按法律争点查找权威案例和裁判规则，例如股东知情权、建设工程优先受偿权、格式条款效力。",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue": {"type": "string", "description": "法律争点或裁判规则关键词"},
                    "top_k": {"type": "integer", "description": "返回结果数量，默认 6", "default": 6},
                    "cat": {"type": "string"},
                    "source": {"type": "string"},
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                },
                "required": ["issue"],
            },
        ),
        Tool(
            name="generate_case_citation_brief",
            description="按搜索结果中的案例编号生成可放入类案检索报告、代理意见或法律分析的案例引用摘要。",
            inputSchema={
                "type": "object",
                "properties": {
                    "local_id": {"type": "integer", "description": "案例编号（搜索结果卡片底部显示的编号）"},
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

    if name in ("search_similar_cases", "search_authoritative_cases"):
        query = arguments["query"]
        top_k = arguments.get("top_k", 8 if name == "search_authoritative_cases" else 10)
        filters = {}
        for k in ["cat", "source", "year_min", "year_max", "court_like"]:
            if k in arguments and arguments[k] is not None:
                filters[k] = arguments[k]

        # 1. embed query
        query_vec = embed_single(query)

        # 2. search（两阶段：embedding 粗筛 + reranker 精排）
        search_k = max(top_k * 4, 20) if name == "search_authoritative_cases" else top_k
        candidates = searcher.search(query_vec, top_k=search_k, filters=filters, query_text=query)
        if name == "search_authoritative_cases":
            results = [r for r in candidates if _is_citable_result(r)][:top_k]
            if not results and candidates:
                return [TextContent(type="text", text=_fmt_case_leads(f"权威案例线索：{query}", candidates[:top_k]))]
        else:
            results = candidates[:top_k]

        if not results:
            return [TextContent(type="text", text="未找到带有可引用规则文本的匹配案例。请尝试调整查询或放宽过滤条件。")]

        output_parts = []
        if name == "search_authoritative_cases":
            output_parts.append(f"权威案例与司法规则检索「{query}」—— 返回 {len(results)} 个结果\n")
        else:
            output_parts.append(f"搜索「{query}」—— 返回 {len(results)} 个结果\n")
        output_parts.append(_build_cards(results, with_score=True))
        output_parts.append("")
        cats = set(r.get("cat", "") for r in results)
        sources = set(r.get("source", "") for r in results)
        output_parts.append(
            f" 覆盖 {len(cats)} 个类别 | {len(sources)} 个数据源 | "
            f"综合分 {results[-1].get('combined_score',0):.4f} ~ {results[0].get('combined_score',0):.4f}"
        )
        output_parts.append(
            "\n每条卡片已包含详情摘要、引用摘要和原文链接。"
        )
        output_parts.append("如需展开全文或最终汇总表，可以直接说“查看第 N 条详情”或“整理成表格”。")

        return [TextContent(type="text", text="\n".join(output_parts))]

    elif name == "get_case_detail":
        local_id = arguments["local_id"]
        case = searcher.get_case(local_id)
        if case is None:
            return [TextContent(type="text", text=f"案例 {local_id} 不存在。")]
        return [TextContent(type="text", text=_fmt_case_detail(case))]

    elif name == "find_rules_by_issue":
        issue = arguments["issue"]
        top_k = arguments.get("top_k", 6)
        filters = {}
        for k in ["cat", "source", "year_min", "year_max"]:
            if k in arguments and arguments[k] is not None:
                filters[k] = arguments[k]

        query_vec = embed_single(issue)
        candidates = searcher.search(query_vec, top_k=max(top_k * 4, 20), filters=filters, query_text=issue)
        results = [r for r in candidates if _is_citable_result(r)][:top_k]
        if not results:
            if candidates:
                return [TextContent(type="text", text=_fmt_case_leads(f"争点案例线索：{issue}", candidates[:top_k]))]
            return [TextContent(type="text", text="未找到匹配案例。请调整争点表述或放宽过滤条件。")]
        return [TextContent(type="text", text=_fmt_rule_results(issue, results))]

    elif name == "generate_case_citation_brief":
        local_id = arguments["local_id"]
        case = searcher.get_case(local_id)
        if case is None:
            return [TextContent(type="text", text=f"案例 {local_id} 不存在。")]
        return [TextContent(type="text", text=_fmt_citation_brief(case))]

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
        from starlette.routing import BaseRoute, Match, Mount, Route
        from mcp.server.sse import SseServerTransport
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        sse = SseServerTransport("/messages/")
        streamable_http_manager = StreamableHTTPSessionManager(
            app=server,
            json_response=False,
            stateless=False,
        )

        async def handle_sse_asgi(scope, receive, send):
            async with sse.connect_sse(
                scope, receive, send
            ) as streams:
                await server.run(
                    streams[0], streams[1],
                    server.create_initialization_options(),
                )

        async def health(request):
            from starlette.responses import JSONResponse
            return JSONResponse({"status": "ok"})

        class StreamableHTTPASGIApp:
            def __init__(self, session_manager):
                self.session_manager = session_manager

            async def __call__(self, scope, receive, send):
                await self.session_manager.handle_request(scope, receive, send)

        streamable_http_app = StreamableHTTPASGIApp(streamable_http_manager)

        async def handle_mcp_asgi(scope, receive, send):
            """统一 /mcp 入口：POST/DELETE 走 Streamable HTTP，普通 GET 走 SSE。"""
            method = scope.get("method", "").upper()
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            if method in {"POST", "DELETE"} or b"mcp-session-id" in headers:
                await streamable_http_app(scope, receive, send)
            else:
                await handle_sse_asgi(scope, receive, send)

        class ASGIPathRoute(BaseRoute):
            def __init__(self, path: str, app):
                self.path = path.rstrip("/")
                self.app = app

            def matches(self, scope):
                if scope.get("type") == "http" and scope.get("path", "").rstrip("/") == self.path:
                    return Match.FULL, {}
                return Match.NONE, {}

            def url_path_for(self, name: str, /, **path_params):
                raise KeyError(name)

            async def handle(self, scope, receive, send):
                await self.app(scope, receive, send)

        app = Starlette(
            debug=False,
            lifespan=lambda app: streamable_http_manager.run(),
            routes=[
                Route("/health", health),
                ASGIPathRoute("/mcp", handle_mcp_asgi),
                Mount("/messages/", app=sse.handle_post_message),
                ASGIPathRoute("/mcp/http", streamable_http_app),
            ],
        )

        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
