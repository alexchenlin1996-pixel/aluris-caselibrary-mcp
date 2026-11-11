"""Local, case-led retrieval of comparison candidates and their source passages."""

import html
import math
import re
import sqlite3
import threading
import unicodedata
from collections import defaultdict

import jieba

from search import (
    NON_RULE_HEADINGS,
    SOURCE_RULE_HEADINGS,
    _normalize_case_text,
    _parse_heading,
    authority_profile,
    extract_rule_text,
)

_NUMBERS = "0-9零〇一二两三四五六七八九十百千"
_ARTICLE = rf"第\s*([{_NUMBERS}]+)\s*条(?:\s*之\s*([{_NUMBERS}]+))?"
_VERSION = r"\(\d{4}\s*年?\s*(?:修正|修订|修改|版)\)"
_CITATION = re.compile(
    rf"《([^》\n]{{1,160}})》\s*({_VERSION})?\s*"
    rf"({_ARTICLE}(?:[、，,及和\s]+{_ARTICLE})*)"
)
_GUIDE_NUMBER = re.compile(r"^\[?指导(?:性)?案例\s*第?\s*(\d+)\s*号")
_STOPWORDS = frozenset("""
    是否 如何 能否 可否 什么 哪些 哪种 可以 应当 依法 关于 有关 相关 以及 其中
    该案 本案 案例 案件 人民法院 法院 根据 规定 认定 情形 争议 判断 问题
    双方 一方 事实 情况 主张 承担 提出 认为 进行 存在 属于 构成 不能 不应
    适用 支持 不予 处理 原告 被告 法律 当事人 条件 包括 需要 要求 诉讼
    通过 履行 相关性 本身 并未 仍然 其实质 应负 但其与 重点 事项
""".split())
_SENTENCES = re.compile(r"[^。！？；\n]+[。！？；]?")
_REFERENCE_HEADINGS = {"相关法条", "相关法律条文", "关联索引", "民法典条文指引", "关联法条", "法律依据"}
_cache_lock = threading.Lock()
_cached_index = None


def _compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _number(value):
    if value.isdecimal():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    units = {"十": 10, "百": 100, "千": 1000}
    if all(c in digits for c in value):
        return int("".join(str(digits[c]) for c in value))
    total = digit = 0
    for c in value:
        if c in units:
            total += (digit or 1) * units[c]
            digit = 0
        elif c in digits:
            digit = digits[c]
        else:
            return None
    return total + digit


def _reference_text(value):
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    return re.sub(r"\[\[([^\]\n]+)\]\]", lambda m: m[1].rsplit("|", 1)[-1], text)


def _citations(value):
    found = {}
    for match in _CITATION.finditer(_reference_text(value)):
        title, version, articles = match.group(1, 2, 3)
        inline_version = re.search(_VERSION + r"$", title)
        law = _compact(re.sub(_VERSION + r"$", "", title)).removeprefix("中华人民共和国")
        law = law.replace("〈", "<").replace("〉", ">")
        version_id = re.search(r"\d{4}", version or (inline_version[0] if inline_version else ""))
        for article in re.finditer(_ARTICLE, articles):
            main, suffix = article.groups()
            key = (law, _number(main), _number(suffix) if suffix else None, version_id[0] if version_id else "")
            if key[1] is not None and (not suffix or key[2] is not None):
                found.setdefault(key, f"《{title}》{version or ''}{article[0]}")
    return found


def _identity(case):
    keys = set()
    if case.get("url"):
        keys.add(("url", str(case["url"]).strip().removeprefix("https://").removeprefix("http://")))
    number = _compact(case.get("ah"))
    if re.fullmatch(r"\(\d{4}\).+?号(?:判决|裁定)?", number):
        keys.add(("案号", re.sub(r"(?:判决|裁定)$", "", number)))
    if case.get("source") == "指导案例":
        guide = _GUIDE_NUMBER.match(_compact(case.get("title")))
        number = _compact(case.get("num"))
        if guide or number.isdecimal():
            keys.add(("指导案例", int(guide[1] if guide else number)))
    return keys


def _terms(text):
    if "《" in text:
        text = _CITATION.sub(" ", _reference_text(text))
    text = re.sub(rf"《[^》\n]+》|{_ARTICLE}", " ", text)
    return [
        word for word in jieba.lcut(text, HMM=False)
        if len(word) >= 2 and word not in _STOPWORDS and re.search(r"[\u3400-\u9fff]", word)
        and not re.search(r"[\W_]", word)
    ]


def _passages(rule):
    """Keep short neighboring sentences together for evidence selection."""
    for line in rule.splitlines():
        sentences = list(_SENTENCES.finditer(line))
        for i, match in enumerate(sentences):
            sentence = match[0].strip()
            if len(sentence) >= 15:
                next_end = sentences[i + 1].end() if i + 1 < len(sentences) else match.end()
                excerpt = line[match.start():next_end].strip() if next_end - match.start() <= 470 else sentence
                yield excerpt if len(excerpt) <= 500 else excerpt[:500] + "…"


def _case_text(case):
    """Use explicit reasoning and reference sections, avoiding party submissions and background."""
    rule, section, quality, _ = extract_rule_text(case)
    if quality not in ("A", "B"):
        return [], _citations(case.get("statutes"))
    rules = [(section, rule)]
    headings = SOURCE_RULE_HEADINGS.get(case.get("source"), {})
    reasoning = {name for group in headings.values() for name in group}
    included = reasoning | _REFERENCE_HEADINGS
    known = included | NON_RULE_HEADINGS
    current = ""
    sections = defaultdict(list)
    for line in _normalize_case_text(case.get("full") or "").splitlines():
        title, body = _parse_heading(line)
        if not title and line.strip() in known:
            title = line.strip()
        if title:
            current = title
            line = body
        if current in included and line.strip():
            sections[current].append(line)
    refs = _citations(case.get("statutes"))
    for heading, lines in sections.items():
        content = "\n".join(lines)
        if heading in _REFERENCE_HEADINGS:
            refs.update(_citations(content))
        elif content.strip() != rule:
            rules.append((heading, content))
    return rules, refs


class _FollowupIndex:
    def __init__(self, cases):
        self.cases = cases
        self.evidence = {}
        self.references = defaultdict(set)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            self.db.execute("CREATE VIRTUAL TABLE passages USING fts5(terms, case_id UNINDEXED)")
            self.db.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(passages, 'row')")
            self.db.executemany("INSERT INTO passages VALUES (?, ?)", self._index_rows())
            self.db.commit()
        except BaseException:
            self.db.close()
            raise

    def _index_rows(self):
        for i, case in enumerate(self.cases):
            if case.get("abolished") or not (case.get("url") or case.get("local_file")):
                continue
            rules, refs = _case_text(case)
            if not rules:
                continue
            citations = dict(refs)
            passages = {}
            for section, rule in rules:
                citations.update(_citations(rule))
                for passage in _passages(rule):
                    if passage in passages:
                        continue
                    words = _terms(passage)
                    if words:
                        passages[passage] = (section, frozenset(words))
                        yield " ".join(words), i
            self.evidence[i] = (passages, refs, citations)
            for key in citations:
                self.references[key[:3]].add(i)

    def search(self, local_id, issue, top_k):
        seed = self.cases[local_id]
        issue_terms = set(_terms(issue))
        seed_passages, listed_refs, all_refs = self.evidence.get(local_id, ({}, {}, {}))
        query_refs = _citations(issue)
        frequencies = {}

        def load_frequencies(words):
            if words:
                placeholders = ",".join("?" for _ in words)
                frequencies.update(self.db.execute(
                    f"SELECT term, doc FROM vocab WHERE term IN ({placeholders})", tuple(words)
                ))

        def weight(word):
            return math.log(1 + len(self.cases) / (1 + frequencies.get(word, 0)))

        load_frequencies(issue_terms)

        # Explicit issue controls which part of the seed supplies expansion terms.
        seed_excerpt = max(seed_passages, key=lambda p: sum(
            weight(w) for w in sorted(issue_terms & seed_passages[p][1])
        ), default="")
        section, seed_words = seed_passages.get(seed_excerpt, ("", frozenset()))
        seed_refs = {**listed_refs, **_citations(seed_excerpt)} if issue else all_refs
        if query_refs:
            seed_refs = {key: label for key, label in all_refs.items()
                         if any(_same_article(key, query) for query in query_refs)}
        load_frequencies(seed_words - issue_terms)
        expansion = sorted((w for w in seed_words - issue_terms if frequencies.get(w, 0) > 1),
                           key=lambda w: (frequencies[w], -len(w), w))[:10]
        focus = sorted(issue_terms, key=lambda w: (-weight(w), w))
        if not focus and not query_refs:
            focus = expansion
        terms = list(dict.fromkeys([*focus, *expansion]))[:32]
        lexical = []
        issue_anchored = (not issue or bool(query_refs) or bool(issue_terms & seed_words))
        issue_anchored = issue_anchored and (not query_refs or bool(seed_refs))
        if terms and issue_anchored and not query_refs:
            expression = " OR ".join(f'"{term}"' for term in terms)
            lexical = self.db.execute(
                "SELECT case_id, MIN(score) FROM "
                "(SELECT case_id, rank AS score FROM passages WHERE passages MATCH ? ORDER BY rank LIMIT 160) "
                "GROUP BY case_id ORDER BY MIN(score), case_id",
                (expression,),
            ).fetchall()
        by_article = set().union(*(self.references[key[:3]] for key in seed_refs)) if issue_anchored else set()
        lexical_ranks = {case_id: rank for rank, (case_id, _) in enumerate(lexical, 1)}
        seed_keys = _identity(seed)
        ranked = []
        minimum = min(len(focus), max(2, math.ceil(len(focus) * 0.45)))
        anchors = [w for w in focus if w in seed_words and frequencies.get(w, 0) > 1][:3]
        distinctive = set(anchors)
        # When a rare term distinguishes the issue from a broader topic, keep that term
        # in the evidence instead of letting the broad matches replace it.
        required = set()
        if issue_terms:
            for position, (word, next_word) in enumerate(zip(anchors, anchors[1:]), 1):
                if frequencies[next_word] >= 3 * frequencies[word]:
                    required.update(anchors[:position])
        for case_id in set(lexical_ranks) | by_article:
            if case_id == local_id or seed_keys & _identity(self.cases[case_id]):
                continue
            passages, _, refs = self.evidence[case_id]
            shared = {key[:3]: label for key, label in refs.items()
                      if any(_same_article(key, s) for s in seed_refs)}
            if query_refs and not shared:
                continue
            best = None
            for passage, (source, present) in passages.items():
                matched = present.intersection(focus)
                expanded = present.intersection(expansion)
                if focus and (not matched or len(matched) < (max(1, minimum - 1) if shared else minimum)):
                    continue
                if issue_terms and (not required <= matched or len(matched & distinctive) < min(2, len(distinctive))):
                    continue
                if not issue_terms and not focus and not shared:
                    continue
                score = sum(weight(w) for w in sorted(matched)) + 0.25 * sum(weight(w) for w in sorted(expanded))
                if best is None or score > best[0]:
                    best = (score, passage, source, sorted(matched | expanded))
            if best is None:
                continue
            score, passage, source, matched = best
            score += 0.7 * min(len(shared), 2) + 1.5 / (8 + lexical_ranks.get(case_id, 160))
            score += 0.2 * authority_profile(self.cases[case_id].get("source"))["authority_weight"]
            ranked.append((score, case_id, passage, source, list(shared.values()), matched))
        results = []
        seen = set(seed_keys)
        for _, case_id, passage, source, shared, matched in sorted(ranked, key=lambda row: (-row[0], row[1])):
            keys = _identity(self.cases[case_id])
            if seen & keys:
                continue
            seen.update(keys)
            results.append({"local_id": case_id, "case": self.cases[case_id], "evidence": passage,
                            "section": source, "references": shared[:4], "terms": matched})
            if len(results) >= top_k:
                break
        return {"seed": seed, "seed_excerpt": seed_excerpt, "seed_section": section,
                "references": list(seed_refs.values())[:6], "terms": expansion, "results": results}


def _same_article(left, right):
    return left[:3] == right[:3] and (not left[3] or not right[3] or left[3] == right[3])


def follow_case(cases, local_id, issue="", top_k=5):
    """Use the same case snapshot for expansion and results, including after data refresh."""
    global _cached_index
    if not 0 <= local_id < len(cases) or cases[local_id].get("abolished"):
        return None
    with _cache_lock:
        if _cached_index is None or _cached_index.cases is not cases:
            index = _FollowupIndex(cases)
            if _cached_index is not None:
                _cached_index.db.close()
            _cached_index = index
        return _cached_index.search(local_id, issue, top_k)
