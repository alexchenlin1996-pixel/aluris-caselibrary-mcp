"""
Embedding client — 智谱 GLM 的 embedding-3 模型。
环境变量：ZHIPU_API_KEY（复用用户已有的智谱 Key）
"""

import os
import time
import json
import numpy as np
import httpx
from typing import List

# --- config ---
API_BASE = "https://open.bigmodel.cn/api/paas/v4"
MODEL = "embedding-3"
BATCH_SIZE = 64
DIM = 2048
MAX_RETRIES = 3
RETRY_DELAY = 2.0


def _get_api_key() -> str:
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key:
        raise RuntimeError("ZHIPU_API_KEY 环境变量未设置")
    return key


def embed_single(text: str, client: httpx.Client | None = None) -> List[float]:
    """嵌入单条文本"""
    return embed_batch([text], client=client)[0]


def embed_batch(texts: List[str], client: httpx.Client | None = None) -> List[List[float]]:
    """批量嵌入文本，最多一次 64 条"""
    if not texts:
        return []

    close_client = False
    if client is None:
        client = httpx.Client(timeout=30.0, trust_env=False)
        close_client = True

    api_key = _get_api_key()
    url = f"{API_BASE}/embeddings"

    results = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.post(
                    url,
                    json={
                        "model": MODEL,
                        "input": batch,
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                # 按索引排序
                items = sorted(body["data"], key=lambda x: x["index"])
                results.extend([item["embedding"] for item in items])
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise RuntimeError(f"Embedding API 失败（{len(batch)} 条）: {e}")

    if close_client:
        client.close()

    return results


def build_embeddings(jsonl_path: str, output_path: str, dim: int = DIM) -> int:
    """
    从 JSONL 文件构建全量 embedding 矩阵，存为 .npy。
    返回处理的案例数。
    """
    texts = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            case = json.loads(line.strip())
            # 拼接搜索文本：标题 + 关键词 + 裁判要点
            search_text = " ".join([
                case.get("title", ""),
                case.get("keywords", ""),
                case.get("gist", ""),
            ])
            texts.append(search_text[:1024])  # 截断，避免超长

    print(f"准备嵌入 {len(texts)} 条案例...")
    client = httpx.Client(timeout=60.0, trust_env=False)
    all_embeddings = []

    total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  批次 {batch_num}/{total_batches}（{len(batch)} 条）...", end=" ")
        try:
            batch_vecs = embed_batch(batch, client=client)
            all_embeddings.extend(batch_vecs)
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            raise

    client.close()

    # 存为 numpy 矩阵
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
    import os

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
            texts.append(search_text[:1024])

    if not texts:
        print("无新案例需要 embedding")
        return 0

    print(f"增量嵌入: {len(texts)} 条新案例...")
    client = httpx.Client(timeout=60.0, trust_env=False)
    new_embeddings = []

    total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  批次 {batch_num}/{total_batches}（{len(batch)} 条）...", end=" ")
        try:
            batch_vecs = embed_batch(batch, client=client)
            new_embeddings.extend(batch_vecs)
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            raise

    client.close()

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
