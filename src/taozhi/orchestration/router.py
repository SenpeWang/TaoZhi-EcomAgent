"""查询意图路由决策器：基于确定性规则与语义特征分流。"""
from __future__ import annotations

import re
from enum import Enum


class QueryRoute(str, Enum):
    """查询路由分支。"""
    INTERNAL_RAG = "internal_rag"       # 内部知识库检索
    EXTERNAL_SEARCH = "external_search" # 外部电商/市场搜索
    DIRECT_ANSWER = "direct_answer"     # 问候/常识直答


class QueryRouter:
    """查询路由决策器。"""

    COMMON_GREETING_WORDS = {
        "你好", "您好", "在吗", "hi", "hello", "早上好", "下午好", "晚上好",
        "谢谢", "多谢", "再见", "拜拜", "你是谁", "介绍一下你自己"
    }

    EXTERNAL_KEYWORDS = {
        "1688", "全网", "淘宝", "拼多多", "京东", "竞品价格", "市场行情", "供货商报价"
    }

    def route(self, query: str) -> QueryRoute:
        """确定性意图路由分流。"""
        clean = query.strip().lower()
        if not clean:
            return QueryRoute.DIRECT_ANSWER

        # 检查是否为短问候语（<= 12 字符且包含问候关键词）
        if len(clean) <= 12 and any(g in clean for g in self.COMMON_GREETING_WORDS):
            return QueryRoute.DIRECT_ANSWER

        if any(kw in clean for kw in self.EXTERNAL_KEYWORDS):
            return QueryRoute.EXTERNAL_SEARCH

        return QueryRoute.INTERNAL_RAG