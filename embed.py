"""
Embedding client — fastembed + BAAI/bge-small-zh-v1.5（本地推理，零 API Key）。
首次使用自动从 HuggingFace 下载模型缓存到本地。

BGE 模型需要前缀：case 文本加 "passage: "，搜索查询加 "query: "。
"""

import os
import json
import numpy as np
from typing import List

# --- config ---
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DIM = 512
BATCH_SIZE = 256

_model = None


def _get_model():
    """懒加载单例，首次调用时下载模型"""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        print(f"加载 embedding 模型: {MODEL_NAME}...")
        _model = TextEmbedding(MODEL_NAME)
        print(f"  模型就绪，维度: {DIM}")
    return _model


def embed_single(text: str) -> List[float]:
    """嵌入单条查询文本（加 query 前缀）"""
    return list(_get_model().embed([f"query: {text}"]))[0].tolist()


def embed_batch(texts: List[str]) -> List[List[float]]:
    """批量嵌入查询文本（加 query 前缀）"""
    if not texts:
        return []
    return [vec.tolist() for vec in _get_model().embed([f"query: {t}" for t in texts])]


def build_embeddings(jsonl_path: str, output_path: str, dim: int = DIM) -> int:
    """
    从 JSONL 文件构建全量 embedding 矩阵，存为 .npy。
    案例文本加 "passage: " 前缀（BGE 模型要求）。
    返回处理的案例数。
    """
    texts = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            case = json.loads(line.strip())
            search_text = " ".join([
                case.get("title", ""),
                case.get("keywords", ""),
                case.get("gist", ""),
            ])
            texts.append(f"passage: {search_text[:1024]}")

    print(f"准备嵌入 {len(texts)} 条案例（模型: {MODEL_NAME}, 维度: {dim}）...")
    model = _get_model()
    all_embeddings = list(model.embed(texts, batch_size=BATCH_SIZE))

    matrix = np.array(all_embeddings, dtype=np.float32)
    np.save(output_path, matrix)
    print(f"Embedding 矩阵: {matrix.shape} → {output_path}")
    print(f"文件大小: {os.path.getsize(output_path) / 1024 / 1024:.1f} MB")

    return len(texts)


def load_embeddings(path: str) -> np.ndarray:
    """加载 embedding 矩阵"""
    return np.load(path)


def build_incremental_embeddings(jsonl_path: str, output_path: str, start_index: int) -> int:
    """
    增量构建 embedding：只处理 start_index 之后的案例，追加到现有矩阵。
    """
    # 读取现有矩阵
    if os.path.exists(output_path) and start_index > 0:
        existing = np.load(output_path)
        print(f"现有 embedding 矩阵: {existing.shape}")
    else:
        existing = np.empty((0, DIM), dtype=np.float32)

    # 读取需要处理的案例
    texts = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < start_index:
                continue
            case = json.loads(line.strip())
            search_text = " ".join([
                case.get("title", ""),
                case.get("keywords", ""),
                case.get("gist", ""),
            ])
            texts.append(f"passage: {search_text[:1024]}")

    if not texts:
        print("无新案例需要 embedding")
        return 0

    print(f"增量嵌入: {len(texts)} 条新案例...")
    model = _get_model()
    new_embeddings = list(model.embed(texts, batch_size=BATCH_SIZE))

    # 拼接并保存
    new_matrix = np.array(new_embeddings, dtype=np.float32)
    combined = np.vstack([existing, new_matrix]) if existing.size > 0 else new_matrix
    np.save(output_path, combined)
    print(f"合并后矩阵: {combined.shape} → {output_path}")

    return len(texts)


if __name__ == "__main__":
    import sys
    db = os.environ.get("CASE_DB_PATH", os.path.join(os.path.expanduser("~"), ".myagents/case_db"))
    jsonl = sys.argv[1] if len(sys.argv) > 1 else os.path.join(db, "cases.jsonl")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(db, "embeddings.npy")
    build_embeddings(jsonl, out)
