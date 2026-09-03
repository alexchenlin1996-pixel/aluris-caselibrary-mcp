"""
案例库去重与清理脚本。

用法：
  python dedup_clean.py --dry-run        # 只展示将标记/修复的条目，不写盘
  python dedup_clean.py --apply          # 实际标记 abolished + 修复 gist 串号
  python dedup_clean.py --find-cross     # 只输出「公报案例 ↔ 案例库案例」跨源重复候选

清理方式统一为「标记 abolished=True」（软删除），不物理删行，保证行号不变、
embedding 索引不错位、可回滚。检索端(search.py)会自动过滤 abolished 条目。

数据路径默认 ~/.myagents/case_db/cases.jsonl，可用 CASE_DB_PATH 覆盖。
"""
import os
import re
import sys
import json
from pathlib import Path
from collections import defaultdict

CASE_DB_DIR = Path(os.environ.get("CASE_DB_PATH", Path.home() / ".myagents/case_db"))
CASES_JSONL = CASE_DB_DIR / "cases.jsonl"

# ─── 第 1 步：标记 abolished 的清单（local_id → 原因）───────────────
ABOLISH_LIST = {
    # 非案例混入「公报案例」（6/29 同步引入）
    7380: "非案例：全国人大常委会/最高法审判人员任免名单",
    7381: "非案例：2024年全国法院司法统计公报",
    7382: "非案例：2023年全国法院司法统计公报",
    7383: "非案例：2022年全国法院司法统计公报",
    # 公报案例内部 URL 重复（保留早期完整版 279/281，废弃 6/29 脏版）
    7384: "重复：与 local_id=279 同 URL（珠海某实业案），6/29 脏版 gist 为空壳",
    7385: "重复：与 local_id=281 同 URL（郭某财产损害案），6/29 脏版 gist 为空壳",
    # 指导案例误入「公报案例」（保留 source 正确的「指导案例」版）
    7386: "重复：指导性案例266号误入公报，保留 local_id=13",
    7387: "重复：指导性案例267号误入公报，保留 local_id=12",
    7395: "重复：指导性案例278号误入公报，保留 local_id=1",
    7396: "重复：指导性案例279号误入公报，保留 local_id=0",
    # 案号跨源重复（保留案例库案例 3052，废弃 6/29 公报脏版）
    7394: "重复：案号(2021)最高法民再121号与 local_id=3052 跨源重复，6/29 脏版",
    # 最高检指导性案例完全重复（标题仅差序号前缀，正文/gist 逐字相同）
    7003: "重复：检例第64号「1.杨卫国…」与 7006 正文完全相同",
    7002: "重复：检例第70号「案例一…」与 6999 正文完全相同",
}

# ─── 第 2 步：gist 串号修复（local_id → 处理方式）──────────────────
GIST_FIX = {
    # 检例24号马乐案（利用未公开信息交易）的 gist 被错误填成了 25号于英生申诉案的内容
    6960: "clear",  # 清空错误 gist
}


def load_cases():
    cases = []
    with open(CASES_JSONL, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            # 校验 local_id 字段与行号一致（search 层按行号索引）
            if obj.get("local_id") != i:
                print(f"[WARN] 行 {i} 的 local_id 字段={obj.get('local_id')}，与行号不一致", file=sys.stderr)
            cases.append(obj)
    return cases


def norm(s):
    return re.sub(r"\s+", "", (s or "").strip())


def trigrams(s):
    s = norm(s)
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def apply_abolish_and_gist_fix(cases, dry_run=True):
    print("=" * 70)
    print("第 1 步：标记 abolished（软删除）")
    print("=" * 70)
    for lid, reason in sorted(ABOLISH_LIST.items()):
        if lid >= len(cases):
            print(f"  [ERR] local_id={lid} 越界（总数 {len(cases)}）")
            continue
        c = cases[lid]
        title = (c.get("title") or "")[:40]
        src = c.get("source")
        mark = "  [DRY-RUN]" if dry_run else "  [APPLY ]"
        print(f"{mark} {lid} | {src} | {title}")
        print(f"          理由: {reason}")
        if not dry_run:
            c["abolished"] = True

    print()
    print("=" * 70)
    print("第 2 步：gist 串号修复")
    print("=" * 70)
    for lid, action in sorted(GIST_FIX.items()):
        c = cases[lid]
        title = (c.get("title") or "")[:40]
        print(f"  [{'DRY-RUN' if dry_run else 'APPLY'}] {lid} | {title} | 动作={action}")
        print(f"          原 gist: {(c.get('gist') or '')[:80]}")
        if not dry_run:
            if action == "clear":
                c["gist"] = ""


def find_cross_source_dups(cases, threshold=0.85):
    """公报案例 ↔ 案例库案例 之间，gist 高度相似（同案重复）候选。"""
    gz = [c for c in cases if c.get("source") == "公报案例" and not c.get("abolished")]
    ck = [c for c in cases if c.get("source") == "案例库案例" and not c.get("abolished")]

    gz_clean = [(c, trigrams(c.get("gist"))) for c in gz if len(norm(c.get("gist"))) > 30]
    ck_clean = [(c, trigrams(c.get("gist"))) for c in ck if len(norm(c.get("gist"))) > 30]

    print(f"公报案例(有效gist): {len(gz_clean)} | 案例库案例(有效gist): {len(ck_clean)}")

    pairs = []
    for c_gz, tg in gz_clean:
        for c_ck, tc in ck_clean:
            sim = jaccard(tg, tc)
            if sim >= threshold:
                pairs.append((sim, c_gz, c_ck))

    pairs.sort(key=lambda x: -x[0])
    return pairs


# 第 3 步：跨源去重时，从案例库版合并到公报版的字段
MERGE_FIELDS = ["ah", "court"]


def apply_cross_source_dedup(cases, pairs, dry_run=True):
    """保留公报案例（真名+全文），合并案号/法院字段，废弃案例库案例（匿名）。"""
    print("=" * 70)
    print("第 3 步：跨源去重（保留公报案例 + 合并案号/法院）")
    print("=" * 70)
    for sim, gz, ck in pairs:
        gz_id, ck_id = gz.get("local_id"), ck.get("local_id")
        gz_title = (gz.get("title") or "")[:32]
        ck_title = (ck.get("title") or "")[:32]
        print(f"\n  sim={sim:.3f}")
        print(f"    保留 公报案例  [{gz_id}] {gz_title}")
        print(f"    废弃 案例库案例[{ck_id}] {ck_title}")
        merged = []
        for f in MERGE_FIELDS:
            if not (gz.get(f) or "").strip() and (ck.get(f) or "").strip():
                merged.append(f"{f}: '' → '{ck.get(f)}'")
        if merged:
            print(f"    合并字段: {', '.join(merged)}")
        if not dry_run:
            for f in MERGE_FIELDS:
                if not (gz.get(f) or "").strip() and (ck.get(f) or "").strip():
                    gz[f] = ck[f]
            ck["abolished"] = True


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--dry-run"
    cases = load_cases()
    print(f"加载 {len(cases)} 条案例\n")

    if mode == "--find-cross":
        pairs = find_cross_source_dups(cases)
        print(f"\n跨源重复候选（gist Jaccard≥0.85）：{len(pairs)} 组\n")
        for sim, gz, ck in pairs:
            print(f"  sim={sim:.3f}")
            print(f"    公报案例  [{gz.get('local_id')}]: {gz.get('title')[:40]}  ah={gz.get('ah')}")
            print(f"    案例库案例[{ck.get('local_id')}]: {ck.get('title')[:40]}  ah={ck.get('ah')}")
        return

    if mode == "--apply-cross":
        pairs = find_cross_source_dups(cases)
        apply_cross_source_dedup(cases, pairs, dry_run=False)
        with open(CASES_JSONL, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"\n已写回 {CASES_JSONL}，共 {len(cases)} 条。")
        return

    if mode == "--dry-run-cross":
        pairs = find_cross_source_dups(cases)
        apply_cross_source_dedup(cases, pairs, dry_run=True)
        print("\n[DRY-RUN] 未写入。确认后加 --apply-cross 执行。")
        return

    dry_run = (mode != "--apply")
    apply_abolish_and_gist_fix(cases, dry_run=dry_run)

    if dry_run:
        print("\n[DRY-RUN] 未写入任何修改。确认后加 --apply 执行。")
        return

    # 写回
    with open(CASES_JSONL, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n已写回 {CASES_JSONL}，共 {len(cases)} 条。")


if __name__ == "__main__":
    main()
