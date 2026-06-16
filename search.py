"""
语义检索 — 两阶段：embedding 粗筛 + reranker 精排。
加载 embedding 矩阵 + JSONL 案例数据 → 查询时做内积排序 → reranker 重打分。
"""
# NO_PROXY 中的 [::1] 会导致 huggingface_hub → httpx URL 解析崩溃，直接清除不还原
import os as _os
_os.environ.pop("NO_PROXY", None)
_os.environ.pop("no_proxy", None)

import json
import os
import re
import numpy as np
from typing import List, Dict, Optional, Any

_reranker = None


AUTHORITY_PROFILES = {
    "指导案例": {
        "authority_level": "A",
        "authority_weight": 1.00,
        "authority_type": "最高法指导案例",
        "citation_value": "应优先引用",
        "practice_scene": "类案检索、代理意见、裁判规则论证",
    },
    "公报案例": {
        "authority_level": "A-",
        "authority_weight": 0.94,
        "authority_type": "最高法公报案例",
        "citation_value": "权威参考",
        "practice_scene": "裁判观点论证、类案检索报告",
    },
    "典型案例": {
        "authority_level": "B+",
        "authority_weight": 0.88,
        "authority_type": "最高法典型案例",
        "citation_value": "重要参考",
        "practice_scene": "类案处理方式、政策导向说明",
    },
    "案例库案例": {
        "authority_level": "B",
        "authority_weight": 0.82,
        "authority_type": "最高法案例库案例",
        "citation_value": "重要参考",
        "practice_scene": "同类事实比较、裁判观点补充",
    },
    "最高检指导性案例": {
        "authority_level": "B+",
        "authority_weight": 0.86,
        "authority_type": "最高检指导性案例",
        "citation_value": "重要参考",
        "practice_scene": "刑事、公益诉讼、检察监督规则",
    },
    "最高检典型案例": {
        "authority_level": "B",
        "authority_weight": 0.78,
        "authority_type": "最高检典型案例",
        "citation_value": "参考",
        "practice_scene": "检察办案思路、类案规则说明",
    },
    "法答网": {
        "authority_level": "B-",
        "authority_weight": 0.70,
        "authority_type": "司法问答",
        "citation_value": "辅助参考",
        "practice_scene": "法律适用口径说明、实务问答补充",
    },
}

DEFAULT_AUTHORITY_PROFILE = {
    "authority_level": "C",
    "authority_weight": 0.60,
    "authority_type": "权威公开资料",
    "citation_value": "补充参考",
    "practice_scene": "背景检索、规则线索补充",
}

SOURCE_ALIASES = {
    "指导案例": ("指导案例", "最高法指导"),
    "公报案例": ("公报案例", "最高法公报"),
    "典型案例": ("典型案例", "最高法典型"),
    "案例库案例": ("案例库案例", "最高法案例库"),
    "最高检指导性案例": ("最高检指导", "检察指导"),
    "最高检典型案例": ("最高检典型", "检察典型"),
    "法答网": ("法答网", "司法问答"),
}


def authority_profile(source: str) -> dict:
    """返回来源对应的权威分层信息。"""
    return AUTHORITY_PROFILES.get(source or "", DEFAULT_AUTHORITY_PROFILE)


def enrich_case(case: dict) -> dict:
    """为案例补充权威类型、引用价值和适用场景。"""
    profile = authority_profile(case.get("source", ""))
    rule_text, rule_source, rule_quality, rule_quality_note = extract_rule_text(case)
    return {
        **case,
        **profile,
        "rule_text": rule_text,
        "rule_source": rule_source,
        "rule_quality": rule_quality,
        "rule_quality_note": rule_quality_note,
    }


def _year_value(value) -> Optional[int]:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


EXPLICIT_RULE_FIELDS = {
    "gist": "裁判要点",
}

SOURCE_RULE_HEADINGS = {
    "指导案例": {
        "A": ("裁判要旨", "裁判规则", "裁判要点", "执行实施要点", "要旨", "指导意义"),
        "B": ("裁判理由", "法院认为"),
    },
    "公报案例": {
        "A": ("裁判摘要", "裁判要旨", "裁判规则", "裁判要点", "要旨"),
        "B": ("裁判理由", "法院认为"),
    },
    "典型案例": {
        "A": ("裁判摘要", "裁判要旨", "裁判规则", "裁判要点", "要旨"),
        "B": ("典型意义", "案例意义", "案例分析", "法院裁判", "法院认为", "人民法院裁判"),
    },
    "案例库案例": {
        "A": ("裁判摘要", "裁判要旨", "裁判规则", "裁判要点", "要旨"),
        "B": ("裁判理由", "法院认为", "争议焦点及裁判要旨"),
    },
    "最高检指导性案例": {
        "A": ("要旨", "指导意义", "裁判要旨", "裁判规则", "检察要旨"),
        "B": ("检察履职情况", "监督意见", "典型意义"),
    },
    "最高检典型案例": {
        "A": ("要旨", "指导意义", "检察要旨"),
        "B": ("意义", "典型意义", "案例意义", "监督意见", "检察履职情况", "法院裁判", "监督情况及结果"),
    },
    "法答网": {
        "A": ("答复", "解答", "裁判规则", "要旨"),
        "B": ("法律适用", "处理意见", "意见"),
    },
}

NON_RULE_HEADINGS = {
    "关键词", "基本案情", "案件基本情况", "案例简介", "事实和理由",
    "诉讼请求", "申请人请求", "处理结果", "裁判结果", "执行结果",
    "复议结果", "监督结果", "关联索引", "民法典条文指引",
}

WEAK_RULE_PATTERNS = (
    r"^本案(的)?争议焦点(主要)?(为|是)",
    r"^法院生效裁判认为[：:。]?$",
    r"^仲裁委员会裁决[：:]?(驳回|支持|确认|裁决|决定)",
    r"^人民法院(判决|裁定)[：:]?(驳回|支持|确认|撤销|维持)",
    r"^(驳回|支持|撤销|确认|维持|责令|判处).{0,80}$",
)


def extract_rule_text(case: dict) -> tuple[str, str, str, str]:
    """从结构化字段或原文中提取可核验的规则文本，不做推断生成。"""
    for field, label in EXPLICIT_RULE_FIELDS.items():
        text = _clean_case_text(case.get(field, ""))
        if not text:
            continue
        if _is_citable_rule_text(text):
            return text, label, "A", "结构化裁判规则字段"

    section, heading, quality = _extract_rule_section(case)
    if section:
        return section, heading, quality, "按来源规则从原文段落抽取"

    if case.get("source") == "法答网":
        answer = _clean_case_text(case.get("full", ""))
        if _is_citable_rule_text(answer):
            return answer, "法答网答复正文", "A", "法答网正文直接作为答复依据"

    return "", "", "D", "未提取到可引用规则段落"


def _extract_rule_section(case: dict) -> tuple[str, str, str]:
    text = case.get("full", "")
    if not text:
        return "", "", "D"

    normalized = _normalize_case_text(text)
    lines = normalized.splitlines()
    rules = SOURCE_RULE_HEADINGS.get(case.get("source") or "", {})
    heading_quality = {}
    for quality, headings in rules.items():
        for heading in headings:
            heading_quality[heading] = quality

    for i, line in enumerate(lines):
        heading, inline_body = _parse_heading(line)
        if not heading or heading in NON_RULE_HEADINGS:
            continue
        quality = heading_quality.get(heading)
        if not quality:
            continue

        body_lines = []
        if inline_body:
            body_lines.append(inline_body)
        for next_line in lines[i + 1:]:
            next_heading, _ = _parse_heading(next_line)
            if body_lines and next_heading:
                break
            if next_line.strip():
                body_lines.append(next_line.strip())
        section = _clean_case_text("\n".join(body_lines))
        if section and _is_citable_rule_text(section):
            return section, heading, quality

    return "", "", "D"


def _normalize_case_text(text: str) -> str:
    text = str(text)
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    text = text.replace("\xa0", " ")
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _parse_heading(line: str) -> tuple[str, str]:
    line = line.strip()
    if not line:
        return "", ""

    markdown = re.match(r"^#{1,6}\s*(.+)$", line)
    if markdown:
        content = markdown.group(1).strip()
        heading, body = _split_heading_body(content)
        return _heading_name(heading), body

    bracket = re.match(r"^[【［\[]([^】］\]]+)[】］\]]\s*(.*)$", line)
    if bracket:
        return _heading_name(bracket.group(1)), bracket.group(2).strip()

    numbered = re.match(r"^((?:（[一二三四五六七八九十]+）|[一二三四五六七八九十]+、|\d+[.．、])\s*[^：:]{2,20})[：:]?\s*(.*)$", line)
    if numbered:
        heading = _heading_name(numbered.group(1))
        if heading in NON_RULE_HEADINGS or any(heading in headings for q in SOURCE_RULE_HEADINGS.values() for headings in q.values()):
            return heading, numbered.group(2).strip()

    return "", ""


def _split_heading_body(content: str) -> tuple[str, str]:
    content = content.strip()
    if not content:
        return "", ""

    for heading in _all_known_headings():
        if content == heading:
            return heading, ""
        if content.startswith(heading):
            rest = content[len(heading):].strip(" 　：:")
            if rest:
                return heading, rest
    return content, ""


def _all_known_headings() -> tuple[str, ...]:
    headings = set(NON_RULE_HEADINGS)
    for quality_map in SOURCE_RULE_HEADINGS.values():
        for items in quality_map.values():
            headings.update(items)
    return tuple(sorted(headings, key=len, reverse=True))


def _heading_name(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^(（[一二三四五六七八九十]+）|[一二三四五六七八九十]+、|\d+[.．、])\s*", "", line)
    line = line.strip("【】[] 　：:、，,.．")
    return line


def _clean_case_text(text: str, limit: int = 3000) -> str:
    if not text:
        return ""
    text = _normalize_case_text(text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    return text[:limit]


def _is_citable_rule_text(text: str) -> bool:
    text = _clean_case_text(text, limit=5000)
    if len(text) < 30:
        return False
    for pattern in WEAK_RULE_PATTERNS:
        if re.search(pattern, text):
            return False

    legal_terms = (
        "人民法院", "法院", "检察", "应当", "可以", "不得", "属于", "构成", "认定",
        "适用", "不予支持", "无效", "有效", "责任", "权利", "义务", "证据",
        "行政机关", "劳动者", "用人单位", "合同", "侵权", "执行", "监督",
        "诉讼", "犯罪", "被害人", "违法", "违法所得", "原则上", "计算",
    )
    return any(term in text for term in legal_terms)


def _get_reranker():
    """懒加载 reranker 单例。设置 DISABLE_RERANKER=1 可跳过（内存受限的服务器用）。"""
    global _reranker
    if _reranker is None:
        if os.environ.get("DISABLE_RERANKER", "").strip() in ("1", "true", "yes"):
            print("Reranker 已禁用（DISABLE_RERANKER=1）")
            _reranker = False
        else:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                _reranker = TextCrossEncoder("BAAI/bge-reranker-base")
                print("Reranker 模型就绪: BAAI/bge-reranker-base")
            except Exception as e:
                print(f"Reranker 加载失败（将跳过精排）: {e}")
                _reranker = False
    return _reranker if _reranker is not False else None


class CaseSearcher:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.environ.get("CASE_DB_PATH", os.path.join(os.path.expanduser("~"), ".myagents/case_db"))

        self.db_path = db_path
        self.jsonl_path = os.path.join(db_path, "cases.jsonl")
        self.emb_path = os.path.join(db_path, "embeddings.npy")

        # 懒加载
        self._embeddings: Optional[np.ndarray] = None
        self._emb_norms: Optional[np.ndarray] = None
        self._cases: Optional[List[dict]] = None
        self._last_signature: tuple[float, float] = (0, 0)  # 数据文件修改时间追踪

    def _reload_if_stale(self):
        """检测文件变更，自动热重载数据（适用于远端 HTTP 模式下的在线更新）"""
        if not os.path.exists(self.jsonl_path):
            return
        jsonl_mtime = os.path.getmtime(self.jsonl_path)
        emb_mtime = os.path.getmtime(self.emb_path) if os.path.exists(self.emb_path) else 0
        signature = (jsonl_mtime, emb_mtime)
        if signature != self._last_signature:
            self._cases = None
            self._embeddings = None
            self._emb_norms = None
            self._last_signature = signature

    @property
    def embeddings(self) -> np.ndarray:
        self._reload_if_stale()
        if self._embeddings is None:
            if not os.path.exists(self.emb_path):
                raise FileNotFoundError(f"Embedding 文件不存在: {self.emb_path}。请先运行 build_embeddings。")
            self._embeddings = np.load(self.emb_path)
        return self._embeddings

    @property
    def emb_norms(self) -> np.ndarray:
        self._reload_if_stale()
        if self._emb_norms is None:
            self._emb_norms = np.linalg.norm(self.embeddings, axis=1)
        return self._emb_norms

    @property
    def cases(self) -> List[dict]:
        self._reload_if_stale()
        if self._cases is None:
            self._cases = []
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    self._cases.append(json.loads(line.strip()))
        return self._cases

    def search(
        self,
        query_vec: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        query_text: str = "",
    ) -> List[dict]:
        """
        两阶段搜索：embedding 粗筛 → reranker 精排。
        filters 可选: cat, source, year_min, year_max, court, cause, abolished
        """
        cases = self.cases
        embeddings = self.embeddings
        emb_norms = self.emb_norms
        item_count = min(len(cases), len(embeddings))
        if item_count == 0:
            return []

        query = np.array(query_vec, dtype=np.float32)
        query_norm = query / (np.linalg.norm(query) + 1e-8)

        similarities = np.dot(embeddings[:item_count], query_norm) / (emb_norms[:item_count] + 1e-8)

        # 第一阶段：embedding 粗筛（取 top-N 进入精排和权威排序）。
        # 召回池放宽，后续用软关键词分纠偏，不用硬过滤，避免检索过窄。
        recall_n = max(top_k * 20, 300)
        candidates = []
        for idx, sim in enumerate(similarities[:item_count]):
            case = cases[idx]
            if case.get("abolished"):
                continue
            if filters:
                if not self._match_filters(case, filters):
                    continue
            candidates.append({
                "idx": idx,
                "similarity": float(sim),
                "semantic_score": float(sim),
            })

        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        candidates = candidates[:recall_n]
        normalized_similarities = self._normalize_scores([item["similarity"] for item in candidates])
        for item, norm_score in zip(candidates, normalized_similarities):
            item["semantic_score"] = norm_score

        # 第二阶段：reranker 精排
        reranker = _get_reranker()
        if reranker and query_text and len(candidates) > top_k:
            # 构建文档文本（title + gist，用于精排）
            doc_texts = []
            for item in candidates:
                c = cases[item["idx"]]
                doc_texts.append(f"{c.get('title', '')} {c.get('gist', '')[:512]}")
            # reranker 打分
            raw_scores = [self._rerank_score_value(s) for s in reranker.rerank(query_text, doc_texts)]
            normalized_scores = self._normalize_scores(raw_scores)
            for item, raw_score, norm_score in zip(candidates, raw_scores, normalized_scores):
                item["rerank_score"] = raw_score
                item["semantic_score"] = norm_score
            candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            candidates = candidates[:max(top_k * 4, 30)]

        ranked = self._rank_authoritative(candidates, cases, top_k, query_text, filters)

        return [
            {
                "local_id": item["idx"],
                "similarity": round(item["similarity"], 4),
                "combined_score": round(item["combined_score"], 4),
                "recency_score": round(item["recency_score"], 4),
                "source_match_score": round(item["source_match_score"], 4),
                **{k: enrich_case(cases[item["idx"]]).get(k) for k in [
                    "title", "label", "cat", "cause", "court", "year",
                    "gist", "keywords", "statutes", "source", "ah", "url", "local_file", "abolished", "batch",
                    "authority_level", "authority_weight", "authority_type",
                    "citation_value", "practice_scene", "rule_text", "rule_source",
                    "rule_quality", "rule_quality_note",
                ]},
            }
            for item in ranked
        ]

    def get_case(self, local_id: int) -> Optional[dict]:
        """根据 local_id 获取案例完整信息"""
        if 0 <= local_id < len(self.cases):
            return enrich_case(self.cases[local_id])
        return None

    def filter_only(self, filters: Dict[str, Any], limit: int = 50) -> List[dict]:
        """纯条件过滤，不做语义搜索。默认排除已废止案例。"""
        results = []
        for idx, case in enumerate(self.cases):
            if case.get("abolished"):
                continue
            if self._match_filters(case, filters):
                results.append({
                    "local_id": idx,
                    **{k: enrich_case(case).get(k) for k in [
                        "title", "label", "cat", "cause", "court", "year",
                        "gist", "keywords", "statutes", "source", "ah", "url", "local_file", "abolished", "batch",
                        "authority_level", "authority_weight", "authority_type",
                        "citation_value", "practice_scene", "rule_text", "rule_source",
                        "rule_quality", "rule_quality_note",
                    ]},
                })
                if len(results) >= limit:
                    break
        return results

    def stats(self) -> dict:
        """库存统计（排除已废止案例）"""
        from collections import Counter
        active = [c for c in self.cases if not c.get("abolished")]
        cats = Counter(c.get("cat", "未知") for c in active)
        sources = Counter(c.get("source", "未知") for c in active)
        years = [str(c.get("year")) for c in active if c.get("year")]
        years_sorted = sorted(years, key=lambda y: y.zfill(4))
        return {
            "total": len(active),
            "by_category": dict(cats.most_common()),
            "by_source": dict(sources.most_common()),
            "year_range": f"{years_sorted[0]}-{years_sorted[-1]}" if years_sorted else "N/A",
            "newest_year": years_sorted[-1] if years_sorted else None,
        }

    @staticmethod
    def _match_filters(case: dict, filters: dict) -> bool:
        for key, val in filters.items():
            if key == "year_min":
                year = _year_value(case.get("year"))
                if year is not None and year < int(val):
                    return False
            elif key == "year_max":
                year = _year_value(case.get("year"))
                if year is not None and year > int(val):
                    return False
            elif key == "abolished":
                if case.get("abolished") != val:
                    return False
            elif key == "court_like":
                court = case.get("court", "") or ""
                if val not in court:
                    return False
            else:
                # 精确匹配
                cv = case.get(key)
                if isinstance(val, list):
                    if cv not in val:
                        return False
                elif cv != val:
                    return False
        return True

    def _rank_authoritative(
        self,
        candidates: List[dict],
        cases: List[dict],
        top_k: int,
        query_text: str,
        filters: Optional[Dict[str, Any]],
    ) -> List[dict]:
        """按语义相似度、来源权威、新近程度和来源匹配综合排序。"""
        if not candidates:
            return []

        years = [_year_value(cases[item["idx"]].get("year")) for item in candidates]
        valid_years = [y for y in years if y is not None]
        oldest = min(valid_years) if valid_years else None
        newest = max(valid_years) if valid_years else None

        for item in candidates:
            case = cases[item["idx"]]
            profile = authority_profile(case.get("source", ""))
            recency_score = self._recency_score(case.get("year"), oldest, newest)
            source_match_score = self._source_match_score(case.get("source", ""), query_text, filters)
            query_term_score = self._query_term_score(case, query_text)
            item["authority_score"] = profile["authority_weight"]
            item["recency_score"] = recency_score
            item["source_match_score"] = source_match_score
            item["query_term_score"] = query_term_score
            item["combined_score"] = (
                item.get("semantic_score", item["similarity"]) * 0.35
                + item["authority_score"] * 0.20
                + query_term_score * 0.35
                + recency_score * 0.05
                + source_match_score * 0.05
            )

        candidates.sort(
            key=lambda x: (
                x["combined_score"],
                x["query_term_score"],
                x["authority_score"],
                x["similarity"],
            ),
            reverse=True,
        )
        return candidates[:top_k]

    @staticmethod
    def _recency_score(year, oldest: Optional[int], newest: Optional[int]) -> float:
        y = _year_value(year)
        if y is None or oldest is None or newest is None:
            return 0.5
        if newest == oldest:
            return 1.0
        return max(0.0, min(1.0, (y - oldest) / (newest - oldest)))

    @staticmethod
    def _source_match_score(source: str, query_text: str, filters: Optional[Dict[str, Any]]) -> float:
        if filters and filters.get("source") == source:
            return 1.0

        query = query_text or ""
        for alias in SOURCE_ALIASES.get(source or "", ()):
            if alias and alias in query:
                return 1.0
        if "最高法" in query and source in {"指导案例", "公报案例", "典型案例", "案例库案例"}:
            return 0.8
        if "最高检" in query and source in {"最高检指导性案例", "最高检典型案例"}:
            return 0.8
        if "检察" in query and source in {"最高检指导性案例", "最高检典型案例"}:
            return 0.7
        return 0.0

    @classmethod
    def _query_term_score(cls, case: dict, query_text: str) -> float:
        """关键词软匹配分：用于纠偏语义召回，不作为过滤条件。"""
        terms = cls._query_terms(query_text)
        if not terms:
            return 0.0

        enriched = enrich_case(case)
        text = " ".join(str(v or "") for v in [
            enriched.get("title"),
            enriched.get("cause"),
            enriched.get("keywords"),
            enriched.get("statutes"),
            enriched.get("gist"),
            enriched.get("rule_text"),
            enriched.get("full", "")[:4000],
        ])
        text = re.sub(r"\s+", "", text)
        if not text:
            return 0.0

        matched = 0.0
        total = 0.0
        for term, weight in terms:
            total += weight
            if term and term in text:
                matched += weight
        return matched / total if total else 0.0

    @staticmethod
    def _query_terms(query_text: str) -> List[tuple[str, float]]:
        query = re.sub(r"\s+", "", query_text or "")
        query = re.sub(r"[，。、“”‘’；;：:？?！!（）()\[\]【】《》<>]", " ", query)
        parts = [p for p in query.split() if len(p) >= 2]
        weighted: dict[str, float] = {}

        def add(term: str, weight: float):
            term = term.strip()
            if len(term) < 2:
                return
            weighted[term] = max(weighted.get(term, 0.0), weight)

        for part in parts:
            add(part, 3.0 if len(part) >= 4 else 1.5)
            for size, weight in ((4, 1.6), (3, 1.2), (2, 1.0)):
                if len(part) < size:
                    continue
                for i in range(len(part) - size + 1):
                    add(part[i:i + size], weight)

        legal_terms = (
            "无效", "有效", "合同", "格式条款", "格式", "条款", "违约", "解除",
            "赔偿", "责任", "股东", "知情权", "优先受偿权", "执行异议",
            "公益诉讼", "惩罚性赔偿", "竞业限制", "劳动者", "用人单位",
        )
        for term in legal_terms:
            if term in query:
                add(term, 2.0 if len(term) >= 4 else 1.2)

        return sorted(weighted.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))

    @staticmethod
    def _normalize_scores(scores: List[float]) -> List[float]:
        if not scores:
            return []
        low = min(scores)
        high = max(scores)
        if high == low:
            return [1.0 for _ in scores]
        return [(s - low) / (high - low) for s in scores]

    @staticmethod
    def _rerank_score_value(score) -> float:
        if hasattr(score, "score"):
            return float(score.score)
        return float(score)


_searcher: Optional[CaseSearcher] = None


def get_searcher() -> CaseSearcher:
    global _searcher
    if _searcher is None:
        _searcher = CaseSearcher()
    return _searcher
