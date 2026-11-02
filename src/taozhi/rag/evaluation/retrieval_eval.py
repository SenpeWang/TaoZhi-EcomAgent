"""独立检索质量评估器：提供 HitRate@K、MRR@K、NDCG@K 等无状态指标评测。"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Set


class RetrievalEvaluator:
    """检索评估器。完全独立于在线服务，既可离线跑批，也可在线抽样。"""

    @staticmethod
    def hit_rate(retrieved_ids: List[str], ground_truth_ids: List[str], k: int = 5) -> float:
        """计算 HitRate@K（命中率：前 K 条中是否包含至少一个目标切片）。"""
        if not retrieved_ids or not ground_truth_ids:
            return 0.0
        top_k = set(retrieved_ids[:k])
        target = set(ground_truth_ids)
        return 1.0 if top_k.intersection(target) else 0.0

    @staticmethod
    def mrr(retrieved_ids: List[str], ground_truth_ids: List[str], k: int = 5) -> float:
        """计算 MRR@K（平均倒数排名：首个相关切片的倒数位次）。"""
        if not retrieved_ids or not ground_truth_ids:
            return 0.0
        target = set(ground_truth_ids)
        for rank, chunk_id in enumerate(retrieved_ids[:k], 1):
            if chunk_id in target:
                return 1.0 / rank
        return 0.0

    @staticmethod
    def ndcg(retrieved_ids: List[str], ground_truth_ids: List[str], k: int = 5) -> float:
        """计算二值 NDCG@K。"""
        if not retrieved_ids or not ground_truth_ids:
            return 0.0
        target = set(ground_truth_ids)
        dcg = 0.0
        for rank, chunk_id in enumerate(retrieved_ids[:k], 1):
            if chunk_id in target:
                dcg += 1.0 / math.log2(rank + 1)

        # 理想 DCG
        ideal_hits = min(len(target), k)
        idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
        if idcg == 0.0:
            return 0.0
        return dcg / idcg

    def evaluate_batch(
        self, cases: List[Dict[str, Any]], k: int = 5
    ) -> Dict[str, float]:
        """批量评估测试用例集合并输出聚合指标。"""
        if not cases:
            return {"hit_rate": 0.0, "mrr": 0.0, "ndcg": 0.0, "count": 0}

        total_hr = 0.0
        total_mrr = 0.0
        total_ndcg = 0.0

        for case in cases:
            ret_ids = case.get("retrieved_ids", [])
            gt_ids = case.get("ground_truth_ids", [])
            total_hr += self.hit_rate(ret_ids, gt_ids, k=k)
            total_mrr += self.mrr(ret_ids, gt_ids, k=k)
            total_ndcg += self.ndcg(ret_ids, gt_ids, k=k)

        n = len(cases)
        return {
            f"hit_rate@{k}": round(total_hr / n, 4),
            f"mrr@{k}": round(total_mrr / n, 4),
            f"ndcg@{k}": round(total_ndcg / n, 4),
            "sample_count": n,
        }