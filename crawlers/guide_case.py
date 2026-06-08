"""
指导案例爬虫 — court.gov.cn/fabu/xiangqing/{id}.html
编号连续递增（当前最新：279），抓取逻辑简单。
"""
import re
import httpx
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
BASE_ID_START = 280  # 已知最新编号+1

# court.gov.cn 的指导案例 URL 模式
# 每批案例发布在 /fabu/xiangqing/{id}.html
# 需要从指导案例首页列表发现新编号
GUIDE_LIST_URL = "https://www.court.gov.cn/fabu/gengduo-21.html"


def get_latest_known_number() -> int:
    """从 sync_state 获取已抓取的最新指导案例编号"""
    from sync import load_state
    state = load_state()
    gc = state.get("guide_case", {})
    return gc.get("latest_number", 279)


def fetch_guide_list_page(client: httpx.Client, page: int = 1) -> list:
    """从指导案例列表页提取案例链接和标题"""
    url = f"https://www.court.gov.cn/fabu/gengduo-21-page-{page}.html" if page > 1 else GUIDE_LIST_URL
    try:
        resp = client.get(url, timeout=15.0)
        resp.raise_for_status()
        # 简单的正则匹配：找到 /fabu/xiangqing/{id}.html 链接
        # 匹配模式：<a href="/fabu/xiangqing/数字.html" ...>标题</a>
        pattern = r'<a[^>]*href="(/fabu/xiangqing/(\d+)\.html)"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, resp.text, re.DOTALL)
        cases = []
        for href, num_id, title in matches:
            title = re.sub(r"<[^>]+>", "", title).strip()
            title = re.sub(r"\s+", " ", title)
            if "指导案例" in title and int(num_id) > 200:
                cases.append({
                    "num": int(num_id),
                    "title": title,
                    "url": f"https://www.court.gov.cn{href}",
                })
        return cases
    except Exception as e:
        print(f"  [WARN]  指导案例列表获取失败: {e}")
        return []


def fetch_guide_detail(client: httpx.Client, url: str) -> Optional[dict]:
    """解析指导案例详情页"""
    try:
        resp = client.get(url, timeout=15.0)
        resp.raise_for_status()
        text = resp.text

        # 提取标题
        title_m = re.search(r'<title>(.*?)</title>', text, re.DOTALL)
        title = title_m.group(1).strip() if title_m else ""

        # 提取正文
        body_m = re.search(r'<div class="con_tit">(.*?)<div class="con_bot">', text, re.DOTALL)
        content = body_m.group(1) if body_m else ""
        content = re.sub(r"<[^>]+>", "\n", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

        # 提取关键词
        keywords_m = re.search(r"关键词[：:]([^<\n]+)", content)
        keywords = keywords_m.group(1).strip() if keywords_m else ""

        # 提取裁判要点
        gist_m = re.search(r"裁判要点[：:](.*?)(?=相关法条|$)", content, re.DOTALL)
        gist = gist_m.group(1).strip()[:3000] if gist_m else ""

        return {
            "title": title,
            "keywords": keywords,
            "gist": gist,
            "full": content[:5000],
            "source": "指导案例",
            "cat": "",  # 从标题或关键词推断
            "url": url,
        }
    except Exception as e:
        print(f"  [WARN]  指导案例详情失败: {e}")
        return None


def crawl_incremental(dry_run: bool = False) -> dict:
    """增量抓取新发布的指导案例"""
    latest_known = get_latest_known_number()
    print(f" 已知最新指导案例编号: {latest_known}")

    new_cases = []
    client = httpx.Client(timeout=15.0, trust_env=False, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })

    # 尝试编号 280, 281, 282... 直到连续失败
    for num in range(latest_known + 1, latest_known + 20):
        url = f"https://www.court.gov.cn/fabu/xiangqing/{num}.html"
        try:
            resp = client.head(url, timeout=10.0)
            if resp.status_code != 200:
                break  # 404 说明没有更多了
        except Exception:
            break

        print(f"   指导案例第{num}号...")
        detail = fetch_guide_detail(client, url)
        if detail:
            detail["label"] = f"指导案例{num}号"
            detail["year"] = str(datetime.now(CST).year)
            detail["entry_date"] = datetime.now(CST).strftime("%Y.%m.%d")
            new_cases.append(detail)
            print(f"    [OK]  {detail['title'][:50]}")

    client.close()

    if new_cases and not dry_run:
        from crawlers.case_library import append_to_jsonl, save_sync_state
        from sync import load_state
        append_to_jsonl(new_cases)
        state = load_state()
        state.setdefault("guide_case", {})
        state["guide_case"]["latest_number"] = latest_known + len(new_cases)
        state["guide_case"]["last_sync"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        save_sync_state(state)

    return {"new_count": len(new_cases), "latest_number": latest_known + len(new_cases)}
