"""
从 workspace/最高法案例MCP 导入新案例。
解析 YAML frontmatter + markdown 正文，去重后追加到 JSONL。
"""
import os, sys, json, re, yaml
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WORKSPACE = Path.home() / ".myagents/projects/mino/workspace/最高法案例MCP"
CASES_JSONL = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db")) / "cases.jsonl"

# 目录 -> 来源名映射
SOURCE_MAP = {
    "A1-10 最高法参考案例": "案例库案例",
    "A1-20 最高法指导性案例": "指导案例",
    "A1-30 最高法典型案例": "典型案例",
    "A1-40 最高法公报": "公报案例",
    "A2-10 最高检指导性案例": "最高检指导性案例",
    "A2-20 最高检典型案例": "最高检典型案例",
    "A3-10 法答网": "法答网",
}

# 案件类型关键词映射
CAT_MAP = {"刑事": "刑事", "民事": "民事", "行政": "行政",
           "国家赔偿": "国家赔偿", "执行": "执行"}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta, body)"""
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', text, re.DOTALL)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    return meta, m.group(2).strip()


def extract_case(filepath: Path, source: str) -> dict | None:
    """从单个 md 文件提取案例"""
    try:
        with open(filepath, "r") as f:
            text = f.read()
    except Exception:
        return None

    meta, body = parse_frontmatter(text)
    if not body.strip():
        return None

    # 标题：第一个 ## 行
    title_match = re.search(r'^##\s+(.+)', body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else filepath.stem

    # 跳过目录/索引文件
    if "目录" in title or "inbox" in filepath.name:
        return None
    # 判断是否为实际案例：有案件类型/发文机关 YAML 元数据 或 文件标题含案号
    is_case = bool(
        meta.get("案件类型") or meta.get("发文机关") or
        re.search(r'检例第?\d+号|指导案例第?\d+号|公报案例|[（(]\d{4}[）)]', title)
    )
    if not is_case:
        return None

    # 要旨（尝试多种模式）
    gist = ""
    for pattern in [r'###\s*【要旨】\s*\n(.*?)(?=###|\Z)',
                     r'###\s*【裁判要旨】\s*\n(.*?)(?=###|\Z)',
                     r'###\s*【指导意义】\s*\n(.*?)(?=###|\Z)',
                     r'###\s*【裁判要点】\s*\n(.*?)(?=###|\Z)',
                     r'###\s*【处罚要旨】\s*\n(.*?)(?=###|\Z)']:
        m = re.search(pattern, body, re.DOTALL)
        if m:
            gist = m.group(1).strip()[:3000]
            break

    # 基本案情
    facts = ""
    for pattern in [r'###\s*【基本案情】\s*\n(.*?)(?=###|\Z)',
                     r'###\s*【案情简介】\s*\n(.*?)(?=###|\Z)']:
        m = re.search(pattern, body, re.DOTALL)
        if m:
            facts = m.group(1).strip()[:5000]
            break

    # 关键词
    keywords = ""
    kw_match = re.search(r'###\s*【关键词】\s*\n(.*?)(?=###|$)', body, re.DOTALL)
    if kw_match:
        keywords = kw_match.group(1).strip()[:300].replace("\n", " ")

    # 相关规定
    statutes = ""
    st_match = re.search(r'###\s*【相关规定】\s*\n(.*?)(?=###|\Z)', body, re.DOTALL)
    if st_match:
        statutes = st_match.group(1).strip()[:1500]

    # 案件类型
    cat = ""
    case_type = meta.get("案件类型", "")
    if case_type:
        for k in CAT_MAP:
            if k in case_type:
                cat = CAT_MAP[k]
                break
    if not cat:
        # 从 tags 推断
        tags = meta.get("tags", [])
        if isinstance(tags, list):
            tags_str = " ".join(tags)
        else:
            tags_str = str(tags)
        for k in CAT_MAP:
            if k in tags_str:
                cat = CAT_MAP[k]
                break

    # 日期（可能为 datetime.date 对象）
    date_val = meta.get("发布日期", "") or meta.get("date", "")
    if hasattr(date_val, 'strftime'):
        date = date_val.strftime("%Y-%m-%d")
    else:
        date = str(date_val) if date_val else ""
    year = date[:4] if date else ""

    # 法院/发文机关
    court = meta.get("发文机关", "") or meta.get("court", "")

    # 案号 - 从标题提取
    ah = ""
    ah_match = re.search(r'[（(]\d{4}[）)][^号]+号', title)
    if ah_match:
        ah = ah_match.group(0)

    # 标签
    label = ""
    label_match = re.search(r'[（(](\d{4}[）)][^号]+号)[）)]', title)
    if label_match:
        label = label_match.group(1)
    elif ah:
        label = ah

    return {
        "title": title[:200],
        "label": label or title[:50],
        "source": source,
        "cat": cat,
        "court": court,
        "date": date,
        "year": year,
        "ah": ah,
        "keywords": keywords,
        "gist": gist,
        "full": facts[:5000] if facts else body[:5000],
        "statutes": statutes,
        "entry_date": datetime.now().strftime("%Y.%m.%d"),
        "url": f"file://{filepath}",
        "local_file": str(filepath),
    }


def import_all(dry_run: bool = False) -> dict:
    """导入所有新案例"""
    # 加载已知标题
    known_titles = set()
    if CASES_JSONL.exists():
        with open(CASES_JSONL) as f:
            for line in f:
                try:
                    c = json.loads(line.strip())
                    t = c.get("title", "").strip()
                    if t:
                        known_titles.add(t)
                except Exception:
                    continue

    print(f"已知案例: {len(known_titles)} 条")

    results = {}
    new_all = []

    for dirname, source in SOURCE_MAP.items():
        dpath = WORKSPACE / dirname
        if not dpath.exists():
            continue

        md_files = list(dpath.rglob("*.md"))
        md_files = [f for f in md_files if "目录" not in f.name and "inbox" not in f.name]
        new_for_source = []

        for fp in md_files:
            case = extract_case(fp, source)
            if not case:
                continue
            title = case["title"].strip()
            if title in known_titles:
                continue

            new_for_source.append(case)
            known_titles.add(title)

        print(f"  {source}: {len(md_files)} 文件 -> {len(new_for_source)} 条新案例")
        results[source] = {"files": len(md_files), "new": len(new_for_source)}
        new_all.extend(new_for_source)

    print(f"\n总计: {len(new_all)} 条新案例")

    if new_all and not dry_run:
        # 追加
        existing = sum(1 for _ in open(CASES_JSONL)) if CASES_JSONL.exists() else 0
        with open(CASES_JSONL, "a") as f:
            for i, case in enumerate(new_all):
                case["local_id"] = existing + i
                f.write(json.dumps(case, ensure_ascii=False) + "\n")

        # 增量 embedding
        print(f"生成 embedding (从第{existing}条)...")
        from embed import build_incremental_embeddings
        emb_path = str(CASES_JSONL.parent / "embeddings.npy")
        build_incremental_embeddings(str(CASES_JSONL), emb_path, existing)

    return {"new_total": len(new_all), "by_source": results}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    result = import_all(dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
