"""
增量同步协调器。管理各数据源的同步状态和调度。
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CASE_DB_DIR = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))
SYNC_STATE_PATH = CASE_DB_DIR / "sync_state.json"
CASES_JSONL = CASE_DB_DIR / "cases.jsonl"
EMBEDDINGS_NPY = CASE_DB_DIR / "embeddings.npy"

# 上海时区
CST = timezone(timedelta(hours=8))


def load_state() -> dict:
    if SYNC_STATE_PATH.exists():
        with open(SYNC_STATE_PATH, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_total_cases() -> int:
    if not CASES_JSONL.exists():
        return 0
    with open(CASES_JSONL, "r") as f:
        return sum(1 for _ in f)


def sync_case_library(dry_run: bool = False, build_embeddings: bool = True) -> dict:
    """
    执行 rmfyalk 案例库增量同步，然后重建新增案例的 embedding。
    """
    from sources.case_library import crawl_incremental

    before_count = get_total_cases()
    result = crawl_incremental(dry_run=dry_run, max_pages=30)
    after_count = get_total_cases()

    # 为新增案例生成 embedding
    new_count = result["new_count"]
    if build_embeddings and new_count > 0 and not dry_run:
        print(f"\n 为 {new_count} 条新案例生成 embedding...")
        try:
            from embed import build_incremental_embeddings
            build_incremental_embeddings(str(CASES_JSONL), str(EMBEDDINGS_NPY), before_count)
        except Exception as e:
            print(f"  [ERR]  embedding 生成失败: {e}")
            result.setdefault("errors", []).append(f"embedding: {e}")

    # 更新 state
    state = load_state()
    state.setdefault("case_library", {})
    state["case_library"]["last_sync"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    state["case_library"]["total_cases"] = after_count
    save_state(state)

    result["before_count"] = before_count
    result["after_count"] = after_count
    return result


def sync_all(dry_run: bool = False) -> dict:
    """执行所有数据源的增量同步"""
    results = {
        "timestamp": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "sources": {},
        "total_new": 0,
    }

    # Phase 2.4: rmfyalk
    print("=" * 50)
    print(" rmfyalk 案例库")
    print("=" * 50)
    try:
        cl_result = sync_case_library(dry_run=dry_run, build_embeddings=False)
        results["sources"]["case_library"] = cl_result
        results["total_new"] += cl_result.get("new_count", 0)
    except Exception as e:
        print(f"[ERR]  rmfyalk 同步失败: {e}")
        results["sources"]["case_library"] = {"error": str(e)}

    # 轻量公开源
    for name, desc, crawler_fn in [
        ("guide_case", "指导案例", lambda d: _sync_source("guide_case", d)),
        ("gazette", "公报案例", lambda d: _sync_source("gazette", d)),
        ("fadawang", "法答网", lambda d: _sync_source("fadawang", d)),
    ]:
        print(f"\n{'='*50}")
        print(f" {desc}")
        print("=" * 50)
        try:
            r = crawler_fn(dry_run)
            results["sources"][name] = r
            results["total_new"] += r.get("new_count", 0)
        except Exception as e:
            print(f"[ERR]  {desc} 同步失败: {e}")
            results["sources"][name] = {"error": str(e)}

    # 重建新增 embedding
    total_new = results["total_new"]
    if total_new > 0 and not dry_run:
        print(f"\n 为 {total_new} 条新案例生成 embedding...")
        try:
            from embed import build_incremental_embeddings
            before = sum(1 for _ in open(str(CASES_JSONL))) - total_new
            build_incremental_embeddings(str(CASES_JSONL), str(EMBEDDINGS_NPY), before)
        except Exception as e:
            print(f"  [ERR]  embedding 失败: {e}")

    print(f"\n 全源同步完成: 新增 {results['total_new']} 条")
    return results


def _sync_source(source_name: str, dry_run: bool = False) -> dict:
    """同步单个公开数据源"""
    if source_name == "guide_case":
        from sources.guide_case import crawl_incremental as fn
    elif source_name == "gazette":
        from sources.public_sources import crawl_gazette_incremental as fn
    elif source_name == "fadawang":
        from sources.public_sources import crawl_fadawang_incremental as fn
    else:
        return {"error": f"unknown source: {source_name}"}
    return fn(dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="案例库增量同步")
    parser.add_argument("--dry-run", action="store_true", help="仅检查不写入")
    parser.add_argument("--source", choices=["case_library", "all"], default="all")
    args = parser.parse_args()

    if args.source == "case_library":
        result = sync_case_library(dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = sync_all(dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
