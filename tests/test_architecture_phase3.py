"""阶段三架构重构验收测试：语义缓存直达、独立评估解耦与生成忠实度校验。"""
from __future__ import annotations

import pytest
from taozhi.api.chat import run_chat_service
from taozhi.infrastructure.cache.semantic_cache import SemanticCache
from taozhi.rag.evaluation.faithfulness_eval import FaithfulnessEvaluator
from taozhi.rag.evaluation.retrieval_eval import RetrievalEvaluator
from taozhi.schemas.chat import ChatRequest


def test_semantic_cache_isolation_and_hit():
    """测试独立语义缓存的租户隔离、密级屏障与命中。"""
    cache = SemanticCache(similarity_threshold=0.90)

    # 1. 写入租户 A 的高密级 (level=2) 内容
    cache.set(
        query="iPhone 16 保护壳开模参数",
        tenant_id="tenant_A",
        security_level=2,
        answer="开模深度 0.8mm，高精倒角 0.15mm。",
        citations=[{"doc_id": "d1", "chunk_id": "c1", "doc_title": "保密图纸", "snippet": "..."}],
        vector=[0.1, 0.2, 0.3],
    )

    # 2. 跨租户查询 (tenant_B) -> 未命中
    res_b = cache.get("iPhone 16 保护壳开模参数", tenant_id="tenant_B", security_level=2)
    assert res_b is None

    # 3. 同租户但权限不足 (level=1 < 2) -> 密级拦截未命中
    res_low = cache.get("iPhone 16 保护壳开模参数", tenant_id="tenant_A", security_level=1)
    assert res_low is None

    # 4. 同租户且权限满足 (level=2) -> 命中直达
    res_hit = cache.get("iPhone 16 保护壳开模参数", tenant_id="tenant_A", security_level=2)
    assert res_hit is not None
    assert res_hit["cached"]
    assert "0.8mm" in res_hit["answer"]


def test_retrieval_evaluator_metrics():
    """测试检索评估器的 HitRate@K, MRR@K, NDCG@K 独立算法。"""
    evaluator = RetrievalEvaluator()

    # Case 1: 目标在第 1 位
    assert evaluator.hit_rate(["c1", "c2", "c3"], ["c1"], k=3) == 1.0
    assert evaluator.mrr(["c1", "c2", "c3"], ["c1"], k=3) == 1.0
    assert evaluator.ndcg(["c1", "c2", "c3"], ["c1"], k=3) == 1.0

    # Case 2: 目标在第 2 位
    assert evaluator.hit_rate(["c2", "c1", "c3"], ["c1"], k=3) == 1.0
    assert evaluator.mrr(["c2", "c1", "c3"], ["c1"], k=3) == 0.5
    assert evaluator.ndcg(["c2", "c1", "c3"], ["c1"], k=3) < 1.0

    # Case 3: 目标在第 5 位，k=3 截断 -> 未命中
    assert evaluator.hit_rate(["c2", "c3", "c4", "c5", "c1"], ["c1"], k=3) == 0.0
    assert evaluator.mrr(["c2", "c3", "c4", "c5", "c1"], ["c1"], k=3) == 0.0

    # Case 4: 批量评估
    cases = [
        {"retrieved_ids": ["c1", "c2"], "ground_truth_ids": ["c1"]},
        {"retrieved_ids": ["c3", "c1"], "ground_truth_ids": ["c1"]},
    ]
    batch_res = evaluator.evaluate_batch(cases, k=2)
    assert batch_res["hit_rate@2"] == 1.0
    assert batch_res["mrr@2"] == 0.75  # (1.0 + 0.5) / 2


def test_faithfulness_evaluator():
    """测试生成忠实度与幻觉校验器。"""
    evaluator = FaithfulnessEvaluator()
    chunks = [
        {"content": "本款陶瓷保温杯内胆采用医用级 316 不锈钢，支持 24 小时长效保温与智能触控测温。"}
    ]

    # 有支撑断言
    good_answer = "本款陶瓷保温杯内胆采用医用级 316 不锈钢材料，支持 24 小时保温。"
    report_good = evaluator.evaluate(good_answer, chunks)
    assert report_good.score >= 0.8
    assert not report_good.hallucination_detected

    # 无支撑幻觉断言
    bad_answer = "本款产品内置量子芯片，能够与空间站进行卫星双向直连通信并实现超光速传输。"
    report_bad = evaluator.evaluate(bad_answer, chunks)
    assert report_bad.score < 0.4
    assert report_bad.hallucination_detected


def test_run_chat_service_cache_integration():
    """测试接入层 run_chat_service 的语义缓存直达集成。"""
    cache = SemanticCache()
    req = ChatRequest(question="陶瓷保温杯保温多久？")

    # 首次执行：未命中缓存，走常规编排并写入缓存
    class MockOrchestrator:
        def run(self, state):
            state.answer = "根据产品手册记载 [doc_cup:chk_1]：本产品支持 24 小时持续保温。"
            state.next_node = "end"
            return state

    resp1 = run_chat_service(req, tenant_id="t1", orchestrator=MockOrchestrator(), cache=cache)
    assert not resp1.cached
    assert "24 小时" in resp1.answer

    # 二次执行：相同查询与租户 -> 语义缓存前置拦截直达！
    resp2 = run_chat_service(req, tenant_id="t1", orchestrator=MockOrchestrator(), cache=cache)
    assert resp2.cached
    assert resp2.answer == resp1.answer