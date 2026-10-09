"""Jev 决策引擎（System 1 智能体离散决策内核）。

参考 TypeSafe AI 的 Jev 架构范式，为智能体多任务调度提供高确定性、低延迟、强类型的
离散决策原语（Primitives）：
- Choice: 离散类别/路由选择（支持多选与概率分布输出）
- Score: 连续标量评估（在指定区间内按准则打分）
- Noul: 布尔断言核验（True/False 快速条件裁决）

采用双轨快决策机制（Dual-Track System 1）：
1. Fast Semantic Head: 基于本地语义向量表征与 Softmax 温度标定（~15-30ms，0 Token 成本）；
2. Fast Constrained LLM Head: 在置信度低或多选复杂关系推理时自动升级为 Qwen3.8-Flash 结构化决策（~200-400ms）；
3. Fallback Head: 外部故障时的自愈保底。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field

from .embedding import available as embedding_available, encode_passages, encode_query

logger = logging.getLogger("ecom_copilot.enterprise.jev")


class DecisionPath(str, Enum):
    """决策执行路径。"""
    FAST_SEMANTIC = "fast_semantic"
    FAST_LLM = "fast_llm"
    HEURISTIC_FALLBACK = "heuristic_fallback"


class ChoiceResult(BaseModel):
    """Choice 原语决策结果。"""
    selected: List[str] = Field(description="选中的选项 key 列表")
    confidence: float = Field(ge=0.0, le=1.0, description="决策综合置信度")
    distribution: Dict[str, float] = Field(default_factory=dict, description="所有候选项的概率分布")
    rationale: str = Field(description="决策理由或推导依据")
    path: DecisionPath = Field(description="实际执行路径")
    latency_ms: float = Field(description="决策耗时（毫秒）")


class ScoreResult(BaseModel):
    """Score 原语决策结果。"""
    score: float = Field(description="标量评分")
    confidence: float = Field(ge=0.0, le=1.0, description="评分置信度")
    rationale: str = Field(description="评分理由")
    path: DecisionPath = Field(description="实际执行路径")
    latency_ms: float = Field(description="决策耗时（毫秒）")


class NoulResult(BaseModel):
    """Noul 原语决策结果（布尔断言判断）。"""
    value: bool = Field(description="断言是否成立")
    confidence: float = Field(ge=0.0, le=1.0, description="判断置信度")
    rationale: str = Field(description="判断理由")
    path: DecisionPath = Field(description="实际执行路径")
    latency_ms: float = Field(description="决策耗时（毫秒）")


class JevEngine:
    """Jev 决策引擎核心类。

    提供线程安全的高性能离散决策原语调用与语义向量缓存。
    """

    def __init__(
        self,
        semantic_threshold: float = 0.40,
        margin_threshold: float = 0.12,
        temperature: float = 0.06,
    ) -> None:
        """初始化 Jev 决策引擎。

        Args:
            semantic_threshold: 快速语义路径采纳的最小 Top-1 概率阈值。
            margin_threshold: Top-1 与 Top-2 的最小显著性差值阈值。
            temperature: 语义相似度 Softmax 温度系数。
        """
        self.semantic_threshold = semantic_threshold
        self.margin_threshold = margin_threshold
        self.temperature = temperature
        self._vector_cache: Dict[str, Tuple[List[str], np.ndarray]] = {}
        self._cache_lock = threading.Lock()

    def _hash_options(self, options: Dict[str, str]) -> str:
        """计算选项集合的唯一哈希指纹。"""
        serialized = json.dumps(sorted(options.items()), ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _get_option_vectors(self, options: Dict[str, str]) -> Tuple[List[str], np.ndarray]:
        """获取并缓存候选项的语义向量矩阵。

        Args:
            options: 候选项字典 {key: description}。

        Returns:
            元组 (keys, vectors_matrix)，其中 vectors_matrix 形状为 (N, Dim)。
        """
        opt_hash = self._hash_options(options)
        with self._cache_lock:
            if opt_hash in self._vector_cache:
                return self._vector_cache[opt_hash]

        keys = list(options.keys())
        descriptions = [f"{k}：{options[k]}" for k in keys]
        vectors = encode_passages(descriptions)

        with self._cache_lock:
            self._vector_cache[opt_hash] = (keys, vectors)
        return keys, vectors

    def choice(
        self,
        context: str,
        options: Dict[str, str],
        multi_select: bool = False,
        top_k: int = 1,
        force_llm: bool = False,
        allow_llm: bool = True,
    ) -> ChoiceResult:
        """执行离散选择决策（Choice 原语）。

        根据上下文 context 从 options 候选集中挑选最适合的目标选项。

        Args:
            context: 决策上下文或用户问题。
            options: 候选项字典，格式为 {option_key: description}。
            multi_select: 是否允许多选。
            top_k: 最多选择的选项个数。
            force_llm: 是否强制跳过语义快道直接调用 LLM。
            allow_llm: 是否允许调用外部大模型（若为 False 则保证零外部网络/大模型调用）。

        Returns:
            ChoiceResult: 结构化决策结果，包含选择项、置信度、概率分布及耗时。
        """
        start_time = time.monotonic()
        if not options:
            elapsed = (time.monotonic() - start_time) * 1000
            return ChoiceResult(
                selected=[],
                confidence=0.0,
                distribution={},
                rationale="选项集合为空",
                path=DecisionPath.HEURISTIC_FALLBACK,
                latency_ms=elapsed,
            )

        # 尝试 Fast Semantic 路径（非强制 LLM 时优先）
        if not force_llm and embedding_available():
            try:
                keys, opt_vecs = self._get_option_vectors(options)
                q_vec = encode_query(context)[0]
                sims = np.dot(opt_vecs, q_vec)

                # Softmax 温度标定
                shifted_sims = (sims - np.max(sims)) / self.temperature
                exp_sims = np.exp(shifted_sims)
                probs = exp_sims / np.sum(exp_sims)

                sorted_indices = np.argsort(-probs)
                top_idx = sorted_indices[0]
                second_idx = sorted_indices[1] if len(sorted_indices) > 1 else top_idx

                top_prob = float(probs[top_idx])
                margin = top_prob - float(probs[second_idx]) if len(sorted_indices) > 1 else 1.0

                dist = {keys[i]: round(float(probs[i]), 4) for i in sorted_indices}

                if multi_select and not allow_llm:
                    selected_keys = [keys[i] for i in sorted_indices[:top_k]]
                    elapsed = (time.monotonic() - start_time) * 1000
                    return ChoiceResult(
                        selected=selected_keys,
                        confidence=round(top_prob, 4),
                        distribution=dist,
                        rationale=f"语义向量多选近邻，Top-1 置信度 {top_prob:.1%}",
                        path=DecisionPath.FAST_SEMANTIC,
                        latency_ms=round(elapsed, 2),
                    )

                # 若 Top-1 置信度明确且区分度显著，直接在快路径返回
                if not multi_select and top_prob >= self.semantic_threshold and margin >= self.margin_threshold:
                    elapsed = (time.monotonic() - start_time) * 1000
                    return ChoiceResult(
                        selected=[keys[top_idx]],
                        confidence=round(top_prob, 4),
                        distribution=dist,
                        rationale=f"语义向量近邻直选，置信度 {top_prob:.1%}，优势差 {margin:.1%}",
                        path=DecisionPath.FAST_SEMANTIC,
                        latency_ms=round(elapsed, 2),
                    )
            except Exception as e:
                logger.warning("Jev 语义快道异常: %s", e)

        # 若不允许调用外部 LLM，安全降级至本地模式规则匹配
        if not allow_llm:
            return self._heuristic_choice(context, options, multi_select, top_k, start_time)

        # 升级至 Fast Constrained LLM 路径
        return self._llm_choice(context, options, multi_select, top_k, start_time)

    def _heuristic_choice(
        self,
        context: str,
        options: Dict[str, str],
        multi_select: bool,
        top_k: int,
        start_time: float,
    ) -> ChoiceResult:
        """纯本地启发式规则匹配，绝对不发起任何外部网络/LLM 请求。"""
        matched = []
        for k, desc in options.items():
            words = [k] + [w for w in desc.replace("：", " ").replace("、", " ").replace("，", " ").split() if len(w) >= 2]
            if any(w in context for w in words):
                matched.append(k)
        if not matched:
            matched = [list(options.keys())[0]]
        selected = matched[:top_k] if multi_select else [matched[0]]
        elapsed = (time.monotonic() - start_time) * 1000
        dist = {k: 1.0 / len(selected) for k in selected}
        return ChoiceResult(
            selected=selected,
            confidence=0.6,
            distribution=dist,
            rationale="本地启发式规则匹配兜底（零模型外部调用）",
            path=DecisionPath.HEURISTIC_FALLBACK,
            latency_ms=round(elapsed, 2),
        )

    def _llm_choice(
        self,
        context: str,
        options: Dict[str, str],
        multi_select: bool,
        top_k: int,
        start_time: float,
    ) -> ChoiceResult:
        """调用结构化 Fast LLM 进行约束型离散决策。"""
        from ..llm import LLMClient, ModelTier
        from ..llm.json_utils import extract_json

        options_desc = "\n".join([f"- {k}: {v}" for k, v in options.items()])
        select_mode = f"选择 1 到 {top_k} 个最匹配的选项" if multi_select else "选择 1 个最匹配的选项"

        system_prompt = f"""你是智能体决策核心（Jev System 1 Decision Primitive: Choice）。
请根据输入上下文，从候选项中做出强类型离散裁决。

【候选项集合】
{options_desc}

【决策要求】
1. {select_mode}；
2. 必须只输出严格的 JSON 对象，禁止输出任何代码块标记或额外字符；
3. 输出格式：
{{"selected": ["opt_key"], "confidence": 0.95, "rationale": "简明决策依据", "distribution": {{"opt_key": 0.85}}}}
"""
        try:
            client = LLMClient()
            raw = client.chat(f"上下文：{context}", system=system_prompt, tier=ModelTier.FAST, max_tokens=250)
            data = extract_json(raw, expect="object")
            if isinstance(data, dict):
                candidates = data.get("selected", [])
                if isinstance(candidates, str):
                    candidates = [candidates]
                valid = [c for c in candidates if c in options][:top_k]
                if not valid:
                    valid = [list(options.keys())[0]]

                conf = float(data.get("confidence", 0.85))
                conf = max(0.0, min(1.0, conf))
                dist = data.get("distribution", {})
                if not isinstance(dist, dict):
                    dist = {c: 1.0 / len(valid) for c in valid}

                elapsed = (time.monotonic() - start_time) * 1000
                return ChoiceResult(
                    selected=valid,
                    confidence=round(conf, 4),
                    distribution=dist,
                    rationale=str(data.get("rationale", "LLM 结构化决策裁决")),
                    path=DecisionPath.FAST_LLM,
                    latency_ms=round(elapsed, 2),
                )
        except Exception as e:
            logger.error("Jev LLM 决策失败: %s", e)

        # 降级兜底
        elapsed = (time.monotonic() - start_time) * 1000
        fallback_key = list(options.keys())[0]
        return ChoiceResult(
            selected=[fallback_key],
            confidence=0.5,
            distribution={fallback_key: 1.0},
            rationale="系统降级采用首选默认项",
            path=DecisionPath.HEURISTIC_FALLBACK,
            latency_ms=round(elapsed, 2),
        )

    def score(
        self,
        context: str,
        criterion: str,
        scale: Tuple[float, float] = (0.0, 1.0),
    ) -> ScoreResult:
        """执行标量打分评估（Score 原语）。

        评估 context 是否满足 criterion 指定的评估准则。

        Args:
            context: 待评估内容。
            criterion: 评估维度与打分规则。
            scale: 允许的分值区间 (min, max)。

        Returns:
            ScoreResult: 标量分值、置信度与评估理由。
        """
        start_time = time.monotonic()
        from ..llm import LLMClient, ModelTier
        from ..llm.json_utils import extract_json

        min_val, max_val = scale
        system_prompt = f"""你是智能体决策核心（Jev System 1 Decision Primitive: Score）。
请根据评估准则，对目标内容进行客观打分，范围为 [{min_val}, {max_val}]。

【评估准则】
{criterion}

【输出格式】
必须只输出严格的 JSON 对象：
{{"score": 0.85, "confidence": 0.90, "rationale": "打分理由"}}
"""
        try:
            client = LLMClient()
            raw = client.chat(f"内容：{context}", system=system_prompt, tier=ModelTier.FAST, max_tokens=200)
            data = extract_json(raw, expect="object")
            if isinstance(data, dict):
                score_val = float(data.get("score", (min_val + max_val) / 2))
                score_val = max(min_val, min(max_val, score_val))
                conf = float(data.get("confidence", 0.85))
                elapsed = (time.monotonic() - start_time) * 1000
                return ScoreResult(
                    score=round(score_val, 4),
                    confidence=round(conf, 4),
                    rationale=str(data.get("rationale", "标准评分核验")),
                    path=DecisionPath.FAST_LLM,
                    latency_ms=round(elapsed, 2),
                )
        except Exception as e:
            logger.error("Jev Score 执行异常: %s", e)

        elapsed = (time.monotonic() - start_time) * 1000
        return ScoreResult(
            score=(min_val + max_val) / 2,
            confidence=0.5,
            rationale="系统降级默认中值评分",
            path=DecisionPath.HEURISTIC_FALLBACK,
            latency_ms=round(elapsed, 2),
        )

    def noul(
        self,
        context: str,
        assertion: str,
    ) -> NoulResult:
        """执行布尔断言裁决（Noul 原语）。

        核验在当前 context 条件下，断言 assertion 是否成立（True / False）。

        Args:
            context: 背景事实或上下文。
            assertion: 待核验的具体断言。

        Returns:
            NoulResult: 布尔真值、置信度与推导理由。
        """
        start_time = time.monotonic()
        from ..llm import LLMClient, ModelTier
        from ..llm.json_utils import extract_json

        system_prompt = f"""你是智能体决策核心（Jev System 1 Decision Primitive: Noul/Bool）。
请核验断言在给定上下文中是否成立（true 为成立/满足，false 为不成立/不满足/违规）。

【待核验断言】
{assertion}

【输出格式】
必须只输出严格的 JSON 对象：
{{"value": true, "confidence": 0.95, "rationale": "判定理由"}}
"""
        try:
            client = LLMClient()
            raw = client.chat(f"上下文：{context}", system=system_prompt, tier=ModelTier.FAST, max_tokens=200)
            data = extract_json(raw, expect="object")
            if isinstance(data, dict):
                val = bool(data.get("value", False))
                conf = float(data.get("confidence", 0.9))
                elapsed = (time.monotonic() - start_time) * 1000
                return NoulResult(
                    value=val,
                    confidence=round(conf, 4),
                    rationale=str(data.get("rationale", "断言裁决完成")),
                    path=DecisionPath.FAST_LLM,
                    latency_ms=round(elapsed, 2),
                )
        except Exception as e:
            logger.error("Jev Noul 执行异常: %s", e)

        elapsed = (time.monotonic() - start_time) * 1000
        return NoulResult(
            value=False,
            confidence=0.5,
            rationale="系统降级默认判定不成立",
            path=DecisionPath.HEURISTIC_FALLBACK,
            latency_ms=round(elapsed, 2),
        )


# 单例全局引擎实例
_global_jev_engine: Optional[JevEngine] = None
_engine_lock = threading.Lock()


def get_jev_engine() -> JevEngine:
    """获取 Jev 决策引擎的全局单例实例。"""
    global _global_jev_engine
    if _global_jev_engine is None:
        with _engine_lock:
            if _global_jev_engine is None:
                _global_jev_engine = JevEngine()
    return _global_jev_engine
