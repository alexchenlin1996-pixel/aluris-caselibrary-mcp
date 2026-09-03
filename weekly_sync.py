"""
每周同步脚本：检查登录态 → 同步 rmfyalk → 生成 embedding → 部署到生产服务器。

供 MyAgents 定时任务（每周六）调用，一条命令完成整个闭环。
所有步骤幂等、失败即报错返回非零退出码，避免后台任务静默超时。

用法：
  python weekly_sync.py            # 完整同步（检查→同步→embedding→部署）
  python weekly_sync.py --check    # 只检查登录态和是否有新案例，不写入
"""
import os
import re
import sys
import json
import subprocess
from pathlib import Path

CASE_DB = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))
APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
PYTHON = REPO_DIR / ".venv" / "bin" / "python"
MODEL_DIR = CASE_DB / "bge_model"

SERVER = "root@47.250.43.130"
SERVER_DATA = "/data"


def run(cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """运行 shell 命令，捕获输出。"""
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )


def case_count() -> int:
    with open(CASE_DB / "cases.jsonl", "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def check_login_and_new() -> dict:
    """dry-run 检查登录态和是否有新案例。"""
    r = run(f"cd {APP_DIR} && {PYTHON} sources/case_library.py --dry-run --max-pages 1", timeout=180)
    out = r.stdout + r.stderr

    if "未登录" in out or "RuntimeError" in out:
        return {"login_ok": False, "detail": out.strip()[-300:]}

    m = re.search(r"(\d+) 条新案例待入库", out)
    new_count = int(m.group(1)) if m else 0
    return {"login_ok": True, "new_count": new_count, "detail": out.strip()[-300:]}


def sync_cases() -> int:
    """正式同步 rmfyalk 新案例，返回同步前的案例数（供 embedding 增量使用）。"""
    before = case_count()
    r = run(f"cd {APP_DIR} && {PYTHON} sources/case_library.py --max-pages 30", timeout=600)
    out = r.stdout + r.stderr
    print(out)
    if "同步完成" not in out and "无新案例" not in out:
        raise RuntimeError(f"同步异常：{out.strip()[-300:]}")
    return before


def build_embeddings(start_index: int):
    """为 start_index 之后的新案例生成 embedding。"""
    env = os.environ.copy()
    if MODEL_DIR.exists():
        env["EMBEDDING_MODEL_PATH"] = str(MODEL_DIR)
    code = (
        "import sys; sys.path.insert(0,'app'); "
        "from embed import build_incremental_embeddings; "
        f"build_incremental_embeddings('{CASE_DB / 'cases.jsonl'}','{CASE_DB / 'embeddings.npy'}',{start_index})"
    )
    r = subprocess.run(
        f"{PYTHON} -c \"{code}\"", shell=True, capture_output=True, text=True,
        timeout=900, env=env, cwd=str(REPO_DIR),
    )
    print(r.stdout)
    if r.returncode != 0:
        raise RuntimeError(f"embedding 生成失败：{r.stderr[-300:]}")


def deploy_to_server():
    """把本地数据推到服务器并重启服务。"""
    local_cases = CASE_DB / "cases.jsonl"
    local_emb = CASE_DB / "embeddings.npy"

    r1 = run(f"scp -o ConnectTimeout=10 {local_cases} {SERVER}:{SERVER_DATA}/cases.jsonl.new", timeout=300)
    r2 = run(f"scp -o ConnectTimeout=10 {local_emb} {SERVER}:{SERVER_DATA}/embeddings.npy.new", timeout=300)
    if r1.returncode != 0 or r2.returncode != 0:
        raise RuntimeError(f"scp 失败：{r1.stderr[-200:]} {r2.stderr[-200:]}")

    r3 = run(
        f"ssh -o ConnectTimeout=10 {SERVER} "
        f"'mv {SERVER_DATA}/cases.jsonl.new {SERVER_DATA}/cases.jsonl && "
        f"mv {SERVER_DATA}/embeddings.npy.new {SERVER_DATA}/embeddings.npy && "
        f"systemctl restart caselibrary.service'",
        timeout=120,
    )
    if r3.returncode != 0:
        raise RuntimeError(f"服务器替换/重启失败：{r3.stderr[-300:]}")
    print("部署完成：服务器已重启")


def main():
    check_only = "--check" in sys.argv
    print(f"=== 法随案例库每周同步（check_only={check_only}）===\n")

    # 1. 检查登录态 + 新案例
    print("[1/4] 检查 rmfyalk 登录态和新案例...")
    state = check_login_and_new()
    if not state["login_ok"]:
        print(f"\n[结果] 登录态过期，无法同步。请手动重新登录：")
        print(f"       cd {APP_DIR} && {PYTHON} sources/case_library.py --login")
        print(f"       详情：{state['detail']}")
        sys.exit(2)  # 非零退出码，明确失败

    new_count = state["new_count"]
    print(f"       登录态有效，新案例 {new_count} 条")

    if check_only or new_count == 0:
        print("\n[结果] 无新案例，无需同步。")
        sys.exit(0)

    # 2. 同步
    print(f"\n[2/4] 同步 {new_count} 条新案例...")
    before = sync_cases()
    after = case_count()
    print(f"       同步完成：{before} -> {after}（+{after - before}）")

    # 3. embedding
    print("\n[3/4] 生成新案例 embedding...")
    build_embeddings(before)

    # 4. 部署
    print("\n[4/4] 部署到生产服务器...")
    deploy_to_server()

    print(f"\n[完成] 本周同步完成：新增 {after - before} 条，已部署到服务器。")


if __name__ == "__main__":
    main()
