"""
rmfyalk.court.gov.cn 案例库增量同步。
使用站内 JSON API 获取列表（与浏览器正常行为一致），详情页用 JS 提取。
每周一次增量，低频率不触发任何限制。
"""
import os
import sys
import json
import time
import re
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext

BASE_URL = "https://rmfyalk.court.gov.cn"
LIST_API = f"{BASE_URL}/cpws_al_api/api/cpwsAl/search"
CASE_DB_DIR = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))
PROFILE_DIR = CASE_DB_DIR / "playwright_profile"
CASES_JSONL = CASE_DB_DIR / "cases.jsonl"
SYNC_STATE_PATH = CASE_DB_DIR / "sync_state.json"
REQUEST_DELAY = 2.5  # 页面请求间隔（秒），模拟正常浏览


# ─── sync state ────────────────────────────────────────────────

def load_sync_state() -> dict:
    if SYNC_STATE_PATH.exists():
        with open(SYNC_STATE_PATH, "r") as f:
            return json.load(f)
    return {}

def save_sync_state(state: dict):
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_existing_labels() -> set:
    """从 JSONL 加载已存在的入库编号集合"""
    labels = set()
    if CASES_JSONL.exists():
        with open(CASES_JSONL, "r") as f:
            for line in f:
                try:
                    case = json.loads(line.strip())
                    lbl = case.get("label", "")
                    if lbl:
                        labels.add(lbl)
                except json.JSONDecodeError:
                    continue
    return labels

def append_to_jsonl(cases: list):
    """追加新案例到 JSONL"""
    CASES_JSONL.parent.mkdir(parents=True, exist_ok=True)
    existing_count = 0
    if CASES_JSONL.exists():
        with open(CASES_JSONL, "r") as f:
            existing_count = sum(1 for _ in f)
    with open(CASES_JSONL, "a") as f:
        for case in cases:
            case["local_id"] = existing_count
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
            existing_count += 1


# ─── login ─────────────────────────────────────────────────────

def is_logged_in(page: Page) -> bool:
    try:
        page.goto(f"{BASE_URL}/view/list.html", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        return bool(re.search(r"1[3-9]\d{9}", body))
    except Exception:
        return False

def do_login(pw, headless: bool = False) -> tuple[BrowserContext, Page]:
    """启动浏览器并确保已登录。
    headless=True: 无头模式（cron/自动化），依赖已保存的 cookie；未登录则直接报错。
    headless=False: 有头模式（手动/向导），未登录则轮询等待用户完成登录。
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    context = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = context.new_page()
    if is_logged_in(page):
        print("[OK]  已有有效登录态" + (" (headless)" if headless else ""))
        return context, page

    if headless:
        context.close()
        raise RuntimeError(
            "rmfyalk 未登录且当前为无头模式，无法交互。"
            "请先手动运行 `python sources/case_library.py --login` 完成登录。"
        )

    # 有头模式：轮询等待用户登录（不依赖 stdin）
    try:
        page.goto(f"{BASE_URL}/view/list.html", wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"[WARN] 页面加载失败（{e}），请在浏览器中手动导航并登录")
    page.wait_for_timeout(2000)
    print("\n[WARN] 请在弹出的浏览器中完成登录...")
    print("      （等待最多 180 秒，登录成功后自动继续）")

    for i in range(180):
        time.sleep(1)
        try:
            body = page.inner_text("body")
            if re.search(r"1[3-9]\d{9}", body):
                print(f"[OK]  登录成功（用时 {i + 1} 秒）")
                return context, page
        except Exception:
            pass

    context.close()
    raise RuntimeError("登录超时：180 秒内未检测到登录成功")


# ─── list API ──────────────────────────────────────────────────

def _clean_html(text: str) -> str:
    """去除 HTML 标签，合并空白"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_list_via_api(page: Page, page_num: int = 1,
                       page_size: int = 10) -> dict:
    """
    通过站内搜索 API 获取案例列表（含裁判要旨）。
    API 回的是 JSON，不涉及任何 DOM 解析。
    """
    result = {"total": 0, "cases": []}
    try:
        resp = page.evaluate("""
            async (params) => {
                const resp = await fetch('https://rmfyalk.court.gov.cn/cpws_al_api/api/cpwsAl/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        page: params.page,
                        size: params.size,
                        sortType: 1,
                        searchCondition: ''
                    })
                });
                return await resp.json();
            }
        """, {"page": page_num, "size": page_size})

        if not resp or resp.get("code") != 0:  # code is int 0
            print(f"  API 异常: {resp.get('msg', resp)}")
            return result

        data = resp.get("data", {})
        result["total"] = data.get("totalCount", 0)

        for item in data.get("datas", []):
            case_type = item.get("cpws_al_type", "")
            encoded_id = item.get("id", "")
            rk_time = item.get("cpws_al_rk_time", "")
            zs_date = item.get("cpws_al_zs_date", "")
            # entry_date 从 rk_time 提取日期部分
            entry_date = rk_time[:10] if rk_time else ""

            gist_html = item.get("cpws_al_cpyz", "")
            gist = _clean_html(gist_html)

            keywords = " ".join([
                item.get("cpws_al_case_sort_name", ""),
                item.get("cpws_al_sort_name", ""),
            ]).strip()

            case = {
                "label": item.get("cpws_al_no", ""),
                "title": item.get("cpws_al_title", ""),
                "cat": item.get("cpws_al_case_sort_name", ""),
                "cause": item.get("cpws_al_sort_name", ""),
                "court": item.get("cpws_al_slfy_name", ""),
                "date": zs_date,
                "ah": item.get("cpws_al_ajzh", ""),
                "procedure": item.get("cpws_al_slcx_name", ""),
                "keywords": keywords,
                "gist": gist,
                "entry_date": entry_date,
                "year": (zs_date.split(".")[0] if zs_date else ""),
                "source": "指导案例" if case_type == "01" else "案例库案例",
                "url": f"{BASE_URL}/view/content.html?id={encoded_id}&lib=ck",
            }
            result["cases"].append(case)

    except Exception as e:
        print(f"  API 调用失败: {e}")

    return result


# ─── main crawl ────────────────────────────────────────────────

def crawl_incremental(since_date: Optional[str] = None,
                      max_pages: int = 15,
                      dry_run: bool = False,
                      headless: bool = True) -> dict:
    """增量同步主逻辑。headless=True 用于 cron/自动化（需已有登录态）。"""
    state = load_sync_state()
    cl_state = state.get("case_library", {})
    known_labels = load_existing_labels()

    if since_date is None:
        since_date = cl_state.get("last_entry_date", "2000.01.01")
    print(f" 起始日期: {since_date}  |  已知案例: {len(known_labels)} 条")

    new_cases = []
    latest_entry_date = since_date
    errors = []

    with sync_playwright() as pw:
        context, page = do_login(pw, headless=headless)

        try:
            for page_num in range(1, max_pages + 1):
                print(f"\n API 第 {page_num} 页...")
                result = fetch_list_via_api(page, page_num=page_num)

                api_cases = result["cases"]
                if not api_cases:
                    print("  （无数据，结束）")
                    break

                found_old = False
                for case in api_cases:
                    entry_date = case.get("entry_date", "")
                    label = case.get("label", "")

                    # 跳过已知案例
                    if label in known_labels:
                        found_old = True  # 可能碰到了已知的旧案例
                        continue

                    # 跳过旧案例
                    if entry_date and entry_date <= since_date:
                        found_old = True
                        continue

                    print(f"  [NEW]  {case['title'][:40]} (入库: {entry_date})")
                    new_cases.append(case)
                    known_labels.add(label)

                    if entry_date and entry_date > latest_entry_date:
                        latest_entry_date = entry_date

                if found_old:
                    print("  [STOP]  已到达上次同步日期")
                    break

                if page_num >= max_pages:
                    break

        finally:
            context.close()

    # 保存
    if new_cases and not dry_run:
        print(f"\n 保存 {len(new_cases)} 条新案例...")
        append_to_jsonl(new_cases)
        cl_state["last_entry_date"] = latest_entry_date
        cl_state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state["case_library"] = cl_state
        save_sync_state(state)
        print(f"[OK]  同步完成。最新入库: {latest_entry_date}")

    elif dry_run and new_cases:
        print(f"\n [DRY RUN] {len(new_cases)} 条新案例待入库")
        for c in new_cases[:5]:
            print(f"  - {c.get('title')} ({c.get('entry_date')})")
    else:
        print("[OK]  无新案例")

    return {
        "new_count": len(new_cases),
        "latest_date": latest_entry_date,
        "errors": errors,
    }


def login_guide():
    print("=" * 50)
    print("rmfyalk 案例库 — 登录向导")
    print("=" * 50)
    with sync_playwright() as pw:
        context, page = do_login(pw, headless=False)
        print("[OK]  登录态已保存到:", PROFILE_DIR)
        context.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="rmfyalk 案例库增量同步")
    p.add_argument("--login", action="store_true", help="仅登录")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--since", type=str, help="起始日期 YYYY.MM.DD")
    p.add_argument("--max-pages", type=int, default=15)
    args = p.parse_args()

    if args.login:
        login_guide()
    else:
        result = crawl_incremental(
            since_date=args.since,
            max_pages=args.max_pages,
            dry_run=args.dry_run,
        )
        print(f"\n 新增 {result['new_count']} 条 | 最新 {result['latest_date']} | 错误 {len(result['errors'])}")
