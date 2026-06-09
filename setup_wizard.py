"""
首次启动向导。用户选择初始化模式：
[1] 拉取 GitHub Release 数据包（推荐）
[2] 独立同步
[3] 混合模式（先拉包，后续自己同步）
"""
import os
import json
import sys
from pathlib import Path

CASE_DB_DIR = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))
USER_CONFIG = CASE_DB_DIR / "user_config.json"


def detect_mode() -> str:
    """检测当前模式。返回 'release' | 'crawl' | 'hybrid' | 'unset'"""
    if USER_CONFIG.exists():
        with open(USER_CONFIG) as f:
            cfg = json.load(f)
        return cfg.get("mode", "unset")
    return "unset"


def save_mode(mode: str):
    CASE_DB_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {"mode": mode, "initialized": True}
    with open(USER_CONFIG, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run_wizard(choice: int = 0) -> dict:
    """
    运行初始化向导。choice=0 表示交互式选择。
    返回 {"mode": str, "actions": list}
    """
    if choice == 0:
        print("""
  欢迎使用 法随案例库 MCP

  请选择初始化方式：

  [1] 拉取最新数据包（推荐）
      从 GitHub 下载已打包的数据 + 向量索引
      1 分钟可用，无需额外配置
      适合：开箱即用

  [2] 独立同步模式
      自己登录 rmfyalk + 配 Embedding API
      首次全量需要较长时间
      适合：想要最新数据、不依赖维护者

  [3] 混合模式（推荐进阶）
      先拉数据包起步，之后自己接管增量更新
""")
        try:
            choice = int(input("  请输入 [1/2/3]: ").strip())
        except (EOFError, ValueError):
            choice = 1

    mode_map = {1: "release", 2: "crawl", 3: "hybrid"}
    mode = mode_map.get(choice, "release")
    save_mode(mode)

    actions = []
    if mode in ("release", "hybrid"):
        actions.append("fetch_release_data")
    if mode in ("crawl", "hybrid"):
        actions.append("setup_crawl_env")

    print(f"  模式: {mode}")
    return {"mode": mode, "actions": actions}


def initialize(mode: str = "auto") -> dict:
    """
    自动初始化：检测已有配置，不存在则用默认模式。
    mode='auto' 时检测已有数据决定模式。
    """
    current = detect_mode()
    if current != "unset":
        return {"mode": current, "initialized": True}

    # 检测是否已有数据
    has_data = (CASE_DB_DIR / "cases.jsonl").exists()

    if mode == "auto":
        if has_data:
            save_mode("hybrid")
            print("检测到已有案例数据，模式设为 hybrid")
            return {"mode": "hybrid", "initialized": True}
        else:
            save_mode("release")
            print("未检测到数据，模式设为 release（首次将从 GitHub 拉取）")
            return {"mode": "release", "initialized": True}
    else:
        save_mode(mode)
        return {"mode": mode, "initialized": True}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="法随案例库 初始化向导")
    p.add_argument("--mode", choices=["release", "crawl", "hybrid", "auto"], default="auto")
    p.add_argument("--choice", type=int, choices=[1, 2, 3], help="直接选择模式")
    args = p.parse_args()

    if args.choice:
        result = run_wizard(args.choice)
    elif args.mode != "auto":
        result = initialize(args.mode)
    else:
        result = run_wizard()

    print(json.dumps(result, ensure_ascii=False, indent=2))
