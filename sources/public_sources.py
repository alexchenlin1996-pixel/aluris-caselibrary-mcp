"""
公报案例 + 法答网 轻量同步（均公开可访问，无需登录）
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

# 公报首页包含案例、法规、司法文件、任免等，只保留真正的案例
_NON_CASE_PATTERNS = [
    r"(工作报告|司法统计公报|任免名单)$",          # 报告/统计/任免
    r"(大法官|审判人员).*(公告|名单|任免)",         # 人事任免
    r"^中华人民共和国\S+法$",                      # 法律文本
    r"^全国人民代表大会",                          # 人大文件
    r"^中共中央",                                  # 党中央文件
    r"(印发|关于印发|关于修改|关于审理).*(通知|规定|解释|批复|办法|意见)$",  # 司法解释/通知
    r"最高人民法院工作报告",                        # 工作报告
]


def _is_case(title: str) -> bool:
    """判断公报标题是否为真正的案例（而非法规、司法文件、任免等）"""
    # 指导性案例由 guide_case.py 专门抓取，公报爬虫不应收录（否则会与「指导案例」来源重复）
    if "指导性案例" in title:
        return False
    # 明确是案例的模式
    if "诉" in title and "案" in title:
        return True
    if re.search(r"与.*(纠纷|赔偿|侵权|合同).*案", title):
        return True

    # 排除非案例模式
    for pattern in _NON_CASE_PATTERNS:
        if re.search(pattern, title):
            return False

    # 兜底：标题含"案"且不以文件类关键词结尾
    if "案" in title and not re.search(r"(报告|通知|批复|解释|规定|办法|名单|公告|公报|意见)$", title):
        return True

    return False


def crawl_gazette_incremental(dry_run: bool = False) -> dict:
    """公报案例增量：检查最新一期公报目录"""
    from sync import load_state, save_state
    state = load_state()
    gs = state.get("gazette", {})
    known_hashes = set(gs.get("known_hashes", []))

    client = httpx.Client(timeout=15.0, headers={"User-Agent": UA}, trust_env=False)
    new_cases = []
    skipped = 0

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

            if not _is_case(title):
                print(f"  [SKIP] {title[:50]}（非案例）")
                skipped += 1
                known_hashes.add(hash_id)  # 记住已跳过，避免重复检查
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
        from sources.case_library import append_to_jsonl
        append_to_jsonl(new_cases)
        gs["known_hashes"] = list(known_hashes)
        gs["last_sync"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        state["gazette"] = gs
        save_state(state)

    return {"new_count": len(new_cases), "skipped": skipped}


def _parse_gazette_detail(client: httpx.Client, url: str) -> Optional[dict]:
    try:
        resp = client.get(url, timeout=15.0)
        resp.raise_for_status()
        text = resp.text

        # 只取正文容器 gb_content，避免把导航/面包屑/页脚混进内容
        body_m = re.search(
            r'id="gb_content"[^>]*>(.*?)(?:<div class="footer|</body>)', text, re.DOTALL
        )
        body_html = body_m.group(1) if body_m else text
        content = re.sub(r"<[^>]+>", "\n", body_html)
        content = re.sub(r"[ \t　]+", " ", content)
        content = re.sub(r"\n{2,}", "\n", content).strip()

        # 裁判要旨：从【裁判要旨】到正文（民事判决书/裁定书）之前
        gist = ""
        gist_m = re.search(
            r"【裁判要旨】\s*(.*?)(?:最高人民法院民事判决书|最高人民法院民事裁定书|民事判决书|民事裁定书|\Z)",
            content,
            re.DOTALL,
        )
        if gist_m:
            gist = re.sub(r"\s+", " ", gist_m.group(1)).strip()

        # 全文：完整正文，设上限防异常超长
        full = content[:50000]

        # 案号：合并空白后再匹配（案号可能被换行/空格拆开）
        flat = re.sub(r"\s+", "", content)
        ah_m = re.search(r"[（(]\d{4}[）)][一-龥\d、]{1,24}号", flat)
        ah = ah_m.group(0) if ah_m else ""

        # 年份
        year_m = re.search(r"(\d{4})", ah) if ah else None
        year = year_m.group(1) if year_m else ""

        return {
            "ah": ah,
            "year": year,
            "gist": gist,
            "keywords": "",
            "full": full,
            "cat": "",
            "court": "",
        }
    except Exception:
        return None


# ─── 法答网 (court.gov.cn/zixun/) ──────────────────────────────

# 网站改版后，法答网「精选答问」以批次合集形式发布，原栏目列表页(gengduo-22)已下线，
# 改用站内搜索「法答网精选答问」作为列表来源。
FADAWANG_SEARCH = "https://www.court.gov.cn/search.html?content=%E6%B3%95%E7%AD%94%E7%BD%91%E7%B2%BE%E9%80%89%E7%AD%94%E9%97%AE"


def crawl_fadawang_incremental(dry_run: bool = False) -> dict:
    """法答网增量：通过站内搜索发现新的「精选答问」批次"""
    from sync import load_state, save_state
    state = load_state()
    fs = state.get("fadawang", {})
    known_ids = set(fs.get("known_ids", []))

    client = httpx.Client(timeout=15.0, headers={"User-Agent": UA}, trust_env=False)
    new_cases = []

    try:
        for page in [1, 2, 3]:
            url = f"{FADAWANG_SEARCH}&page={page}"
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text

            # 匹配链接：/zixun/xiangqing/{id}.html
            links = re.findall(r'href="(/zixun/xiangqing/(\d+)\.html)"[^>]*>([^<]+)', text)
            if not links:
                break

            for href, num_id, title in links:
                title = title.strip()
                # 只抓「法答网精选答问」批次，过滤搜索结果里的其他内容
                if num_id in known_ids or "法答网精选答问" not in title:
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

            if len(new_cases) > 0 and page >= 2:
                break  # 已扫到新批次就停，避免翻太多页

    except Exception as e:
        print(f"  [WARN]  法答网抓取失败: {e}")
    finally:
        client.close()

    if new_cases and not dry_run:
        from sources.case_library import append_to_jsonl
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

        # 提取正文区域（网站改版后正文用 <p> 段落，不再有 con_tit 容器）
        paras = re.findall(r"<p[^>]*>(.*?)</p>", text, re.DOTALL)
        lines = [re.sub(r"<[^>]+>", "", p).strip() for p in paras]
        lines = [re.sub(r"\s+", " ", l) for l in lines if l]
        content = "\n".join(lines)

        # 提取问题（第一个「问题X：」段落）
        question = ""
        for line in lines:
            if "问题" in line and ("？" in line or "：" in line):
                question = line[:200]
                break

        return {
            "gist": content[:3000],
            "full": content[:8000],
            "keywords": question,
            "cat": "",
            "court": "",
            "year": "",
        }
    except Exception:
        return None
