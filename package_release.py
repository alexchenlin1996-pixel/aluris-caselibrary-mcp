"""
数据包打包发布工具（维护者使用）。
将 cases.jsonl + embeddings.npy 打包为 GitHub Release。
"""
import os
import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
CASE_DB_DIR = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))


def count_cases() -> int:
    jsonl = CASE_DB_DIR / "cases.jsonl"
    if not jsonl.exists():
        return 0
    with open(jsonl) as f:
        return sum(1 for _ in f)


def get_sync_state() -> dict:
    state_file = CASE_DB_DIR / "sync_state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {}


def package(output_dir: str = ".") -> dict:
    """
    打包数据文件为 ZIP。
    返回 {"path": str, "size_mb": float, "case_count": int, "version": str}
    """
    case_count = count_cases()
    state = get_sync_state()
    version = datetime.now(CST).strftime("data-%Y-%m-%d")

    output_path = Path(output_dir) / f"{version}.zip"

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in ["cases.jsonl", "embeddings.npy"]:
            fpath = CASE_DB_DIR / fname
            if fpath.exists():
                zf.write(fpath, fname)
                print(f"  + {fname} ({fpath.stat().st_size/1024/1024:.1f} MB)")
            else:
                print(f"  [WARN] {fname} 不存在")

        # 附带元信息
        meta = {
            "version": version,
            "case_count": case_count,
            "sync_state": state,
            "packaged_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        }
        zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

    size_mb = output_path.stat().st_size / 1024 / 1024
    result = {
        "path": str(output_path),
        "size_mb": round(size_mb, 1),
        "case_count": case_count,
        "version": version,
    }
    print(f"\n打包完成: {output_path}")
    print(f"  {case_count} 条案例 | {size_mb:.1f} MB | {version}")

    return result


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="打包案例数据用于 GitHub Release")
    p.add_argument("-o", "--output", default=".", help="输出目录")
    args = p.parse_args()
    result = package(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
