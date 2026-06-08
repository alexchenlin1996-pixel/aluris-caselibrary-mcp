"""
公报案例 + 法答网 轻量爬虫（均公开可访问，无需登录）
"""
import re
import json
import httpx
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ─── 公报案例 (gongbao.court.gov.cn) ───────────────────────────

GAZETTE_INDEX = "http://gongbao.court.gov.cn/"


def crawl_gazette_incremental(dry_run: bool = False) -> dict:
    """公报案例增量：检查最新一期公报目录"""
    from sync import load_state, save_state
    state = load_state()
    gs = state.get("gazette", {})
    known_hashes = set(gs.get("known_hashes", []))

    client = httpx.Client(timeout=15.0, headers={"User-Agent": UA}, trust_env=False)
    new_cases = []

    try:
        # 获取公报首页，找最新一期的链接
        resp = client.get(GAZETTE_INDEX)
        resp.raise_for_status()
        text = resp.text

        # 找案例链接：/Details/{hash}.html
        case_links = re.findall(r'href="(/Details/([a-f0-9]+)\.html)"[^>]*>([^<]+)', text, re.IGNORECASE)
        print(f"  公报首页发现 {len(case_links)} 个链接")

        for href, hash_id, title in case_links:
            title = title.strip()
            if hash_id in known_hashes or len(title) < 4:
                continue

            url = f"http://gongbao.court.gov.cn{href}"
            print(f"   {title[:40]}...")
            detail = _parse_gazette_detail(client, url)
            if detail:
                detail["title"] = title
                detail["url"] = url
                detail["source"] = "公报案例"
                detail["label"] = f"公报-{hash_id[:8]}"
                new_cases.append(detail)
                known_hashes.add(hash_id)

    except Exception as e:
        print(f"  [WARN]  公报案例抓取失败: {e}")
    finally:
        client.close()

    if new_cases and not dry_run:
        from crawlers.case_library import append_to_jsonl
        append_to_jsonl(new_cases)
        gs["known_hashes"] = list(known_hashes)
        gs["last_sync"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        state["gazette"] = gs
        save_state(state)

    return {"new_count": len(new_cases)}


def _parse_gazette_detail(client: httpx.Client, url: str) -> Optional[dict]:
    try:
        resp = client.get(url, timeout=15.0)
        resp.raise_for_status()
        text = resp.text

        content = re.sub(r"<[^>]+>", "\n", text)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

        # 提取案号
        ah_m = re.search(r"[（(]\d{4}[）)][一-龥\d]+号", content)
        ah = ah_m.group(0) if ah_m else ""

        # 提取年份
        year_m = re.search(r"(\d{4})", ah) if ah else None
        year = year_m.group(1) if year_m else ""

        return {
            "ah": ah,
            "year": year,
            "gist": content[:3000],
            "keywords": "",
            "full": content[:5000],
            "cat": "",
            "court": "",
        }
    except Exception:
        return None


# ─── 法答网 (court.gov.cn/zixun/) ──────────────────────────────

FADAWANG_LIST = "https://www.court.gov.cn/zixun/gengduo-22.html"


def crawl_fadawang_incremental(dry_run: bool = False) -> dict:
    """法答网增量：扫描列表页发现新知问答"""
    from sync import load_state, save_state
    state = load_state()
    fs = state.get("fadawang", {})
    known_ids = set(fs.get("known_ids", []))

    client = httpx.Client(timeout=15.0, headers={"User-Agent": UA}, trust_env=False)
    new_cases = []

    try:
        for page in [1, 2]:
            url = f"https://www.court.gov.cn/zixun/gengduo-22-page-{page}.html" if page > 1 else FADAWANG_LIST
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text

            # 匹配链接：/zixun/xiangqing/{id}.html
            links = re.findall(r'href="(/zixun/xiangqing/(\d+)\.html)"[^>]*>([^<]+)', text)
            if not links:
                break

            for href, num_id, title in links:
                title = title.strip()
                if num_id in known_ids or len(title) < 5:
                    continue

                detail_url = f"https://www.court.gov.cn{href}"
                print(f"   {title[:50]}")
                detail = _parse_fadawang_detail(client, detail_url)
                if detail:
                    detail["title"] = title
                    detail["url"] = detail_url
                    detail["source"] = "法答网"
                    detail["label"] = f"法答网-{num_id}"
                    new_cases.append(detail)
                    known_ids.add(num_id)

            if len(new_cases) > 0:
                break  # 只扫到有新内容就停

    except Exception as e:
        print(f"  [WARN]  法答网抓取失败: {e}")
    finally:
        client.close()

    if new_cases and not dry_run:
        from crawlers.case_library import append_to_jsonl
        append_to_jsonl(new_cases)
        fs["known_ids"] = list(known_ids)
        fs["last_sync"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        state["fadawang"] = fs
        save_state(state)

    return {"new_count": len(new_cases)}


def _parse_fadawang_detail(client: httpx.Client, url: str) -> Optional[dict]:
    try:
        resp = client.get(url, timeout=15.0)
        resp.raise_for_status()
        text = resp.text

        # 提取正文区域
        body_m = re.search(r'<div class="con_tit">(.*?)<div class="con_bot">', text, re.DOTALL)
        content = body_m.group(1) if body_m else ""
        content = re.sub(r"<[^>]+>", "\n", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

        # 提取问题（通常以"？"或"。"结尾的第一段）
        question = ""
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if line and ("？" in line or "问题" in line):
                question = line[:200]
                break

        return {
            "gist": content[:3000],
            "full": content[:5000],
            "keywords": question,
            "cat": "",
            "court": "",
            "year": "",
        }
    except Exception:
        return None
