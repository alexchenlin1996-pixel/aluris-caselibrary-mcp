"""
从 GitHub Release 下载数据包（cases.jsonl + embeddings.npy）。
用于 mode=release 或 mode=hybrid 的首次初始化。
"""
import os
import sys
import json
import hashlib
import httpx
from pathlib import Path
from typing import Optional

REPO = "alexchenlin1996-pixel/aluris-caselibrary-mcp"
GITHUB_API = f"https://api.github.com/repos/{REPO}"
CASE_DB_DIR = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))


def get_latest_release() -> Optional[dict]:
    """获取最新的 data release"""
    client = httpx.Client(timeout=20.0, headers={
        "User-Agent": "case-library-mcp",
        "Accept": "application/vnd.github+json",
    }, trust_env=False)

    try:
        resp = client.get(f"{GITHUB_API}/releases/latest")
        if resp.status_code != 200:
            print(f"GitHub API 返回 {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"获取 release 失败: {e}")
        return None
    finally:
        client.close()


def download_asset(asset: dict, target_dir: Path) -> bool:
    """下载单个 release asset 到目标目录"""
    url = asset.get("browser_download_url", "")
    name = asset.get("name", "unknown")
    expected_size = asset.get("size", 0)

    print(f"  下载 {name} ({expected_size/1024/1024:.1f} MB)...", end=" ")

    client = httpx.Client(timeout=300.0, headers={
        "User-Agent": "case-library-mcp",
        "Accept": "application/octet-stream",
    }, trust_env=False, follow_redirects=True)

    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            target_dir.mkdir(parents=True, exist_ok=True)
            filepath = target_dir / name
            downloaded = 0
            with open(filepath, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if expected_size > 0:
                        pct = min(100, int(downloaded / expected_size * 100))
                        if pct % 20 == 0:
                            print(f"{pct}%", end=" ")
            print("done")
        return True
    except Exception as e:
        print(f"失败: {e}")
        return False
    finally:
        client.close()


def fetch_latest_data(force: bool = False) -> dict:
    """
    从 GitHub Release 拉取最新数据包。
    返回 {"success": bool, "case_count": int, "message": str}
    """
    CASE_DB_DIR.mkdir(parents=True, exist_ok=True)

    # 检查本地是否已有数据
    existing_jsonl = CASE_DB_DIR / "cases.jsonl"
    existing_npy = CASE_DB_DIR / "embeddings.npy"
    if existing_jsonl.exists() and existing_npy.exists() and not force:
        return {"success": True, "case_count": -1, "message": "数据已存在，跳过下载（用 --force 强制覆盖）"}

    print("检查 GitHub Release ...")
    release = get_latest_release()
    if not release:
        return {"success": False, "case_count": 0, "message": "无法获取 GitHub Release 信息"}

    tag = release.get("tag_name", "unknown")
    print(f"最新数据包: {tag}")

    assets = release.get("assets", [])
    if not assets:
        return {"success": False, "case_count": 0, "message": "Release 中没有数据文件"}

    success = True
    for asset in assets:
        name = asset.get("name", "")
        if name in ("cases.jsonl", "embeddings.npy"):
            if not download_asset(asset, CASE_DB_DIR):
                success = False

    if success:
        # 统计案例数
        case_count = 0
        if existing_jsonl.exists():
            with open(existing_jsonl) as f:
                case_count = sum(1 for _ in f)
        return {"success": True, "case_count": case_count, "message": f"已下载 {case_count} 条案例"}

    return {"success": False, "case_count": 0, "message": "下载失败"}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="从 GitHub Release 拉取案例数据")
    p.add_argument("--force", action="store_true", help="强制覆盖已有数据")
    args = p.parse_args()
    result = fetch_latest_data(force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
