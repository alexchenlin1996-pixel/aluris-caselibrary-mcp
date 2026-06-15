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
import numpy as np
from typing import List, Dict, Optional, Any

_reranker = None


def _get_reranker():
    """懒加载 reranker 单例"""
    global _reranker
    if _reranker is None:
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
        self._cases: Optional[List[dict]] = None
        self._last_mtime: float = 0  # 文件修改时间追踪

    def _reload_if_stale(self):
        """检测文件变更，自动热重载数据（适用于远端 HTTP 模式下的在线更新）"""
        try:
            mtime = os.path.getmtime(self.jsonl_path)
        except OSError:
            return
        if mtime > self._last_mtime:
            self._cases = None
            self._embeddings = None
            self._last_mtime = mtime

    @property
    def embeddings(self) -> np.ndarray:
        self._reload_if_stale()
        if self._embeddings is None:
            if not os.path.exists(self.emb_path):
                raise FileNotFoundError(f"Embedding 文件不存在: {self.emb_path}。请先运行 build_embeddings。")
            self._embeddings = np.load(self.emb_path)
        return self._embeddings

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
        query = np.array(query_vec, dtype=np.float32)
        query_norm = query / (np.linalg.norm(query) + 1e-8)

        emb_norms = np.linalg.norm(self.embeddings, axis=1)
        similarities = np.dot(self.embeddings, query_norm) / (emb_norms + 1e-8)

        # 第一阶段：embedding 粗筛（取 top-N 进入精排）
        recall_n = max(top_k * 3, 20)
        candidates = []
        for idx, sim in enumerate(similarities):
            case = self.cases[idx]
            if case.get("abolished"):
                continue
            if filters:
                if not self._match_filters(case, filters):
                    continue
            candidates.append((idx, float(sim)))

        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[:recall_n]

        # 第二阶段：reranker 精排
        reranker = _get_reranker()
        if reranker and query_text and len(candidates) > top_k:
            # 构建文档文本（title + gist，用于精排）
            doc_texts = []
            for idx, _ in candidates:
                c = self.cases[idx]
                doc_texts.append(f"{c.get('title', '')} {c.get('gist', '')[:512]}")
            # reranker 打分
            scores = list(reranker.rerank(query_text, doc_texts))
            # 重新排序
            scored = list(zip(candidates, scores))
            scored.sort(key=lambda x: x[1], reverse=True)
            candidates = [(idx, sim) for (idx, sim), _ in scored[:top_k]]
        else:
            candidates = candidates[:top_k]

        return [
            {
                "local_id": idx,
                "similarity": round(sim, 4),
                **{k: self.cases[idx].get(k) for k in [
                    "title", "label", "cat", "cause", "court", "year",
                    "gist", "keywords", "statutes", "source", "ah", "url", "abolished", "batch",
                ]},
            }
            for idx, sim in candidates
        ]

    def get_case(self, local_id: int) -> Optional[dict]:
        """根据 local_id 获取案例完整信息"""
        if 0 <= local_id < len(self.cases):
            return self.cases[local_id]
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
                    **{k: case.get(k) for k in [
                        "title", "label", "cat", "cause", "court", "year",
                        "gist", "keywords", "statutes", "source", "ah", "url", "abolished", "batch",
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
                if case.get("year") and case["year"] < val:
                    return False
            elif key == "year_max":
                if case.get("year") and case["year"] > val:
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


_searcher: Optional[CaseSearcher] = None


def get_searcher() -> CaseSearcher:
    global _searcher
    if _searcher is None:
        _searcher = CaseSearcher()
    return _searcher
