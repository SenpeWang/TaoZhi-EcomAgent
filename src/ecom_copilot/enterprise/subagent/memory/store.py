"""跨会话长效记忆与学习规则存储（Long-Term Memory Store）。

遵循 Antigravity (agy) 设计规范：
1. 跨会话持久化沉淀已学习规则 (/learn)、用户偏好与业务实体事实；
2. 支持实体三元组 (Subject, Predicate, Object) 事实库检索；
3. 支持关键词模糊检索与按类别过滤；
4. 严格遵循 Google Style Docstring 规范。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class LongTermMemoryStore:
    """持久化长效记忆仓库，管理全局规则、偏好设定与电商知识事实。"""

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        """初始化持久化记忆库。

        Args:
            storage_dir: 存储根路径，默认位于 data/runtime/memory 或当前工作区。
        """
        if storage_dir is None:
            # 兼容电商项目私有 data 目录
            candidate = Path("/home/sp/TaoZhi-EcomAgent/data/runtime/memory")
            if not candidate.parent.exists():
                candidate = Path.home() / ".agents" / "memory"
            self.storage_dir = candidate
        else:
            self.storage_dir = Path(storage_dir)

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.rules_file = self.storage_dir / "learned_rules.json"
        self.facts_file = self.storage_dir / "entity_facts.json"

        self._ensure_files()

    def _ensure_files(self) -> None:
        """确保存储文件存在。"""
        if not self.rules_file.exists():
            with open(self.rules_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        if not self.facts_file.exists():
            with open(self.facts_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    def learn_rule(
        self,
        rule_text: str,
        category: str = "general",
        source: str = "user_instruction",
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        """沉淀一条持久化学习规则（对照 /learn 斜杠指令行为）。

        Args:
            rule_text: 规则具体表述。
            category: 类别（如 style, constraint, tool_policy, ecom_rule）。
            source: 来源说明。
            confidence: 置信度。

        Returns:
            Dict[str, Any]: 新增的规则条目。
        """
        rules = self.get_rules()
        entry = {
            "id": f"rule_{len(rules) + 1}",
            "text": rule_text.strip(),
            "category": category,
            "source": source,
            "confidence": confidence,
            "learned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        # 避免完全重复
        if not any(r.get("text") == entry["text"] for r in rules):
            rules.append(entry)
            with open(self.rules_file, "w", encoding="utf-8") as f:
                json.dump(rules, f, ensure_ascii=False, indent=2)
        return entry

    def get_rules(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取所有已学得的持久化规则。

        Args:
            category: 可选按类别筛选。

        Returns:
            List[Dict[str, Any]]: 规则列表。
        """
        try:
            with open(self.rules_file, "r", encoding="utf-8") as f:
                items = json.load(f)
                if category:
                    return [it for it in items if it.get("category") == category]
                return items
        except Exception:
            return []

    def remember_fact(
        self,
        subject: str,
        predicate: str,
        object_val: Any,
        confidence: float = 1.0,
        source: str = "agent_discovery",
    ) -> Dict[str, Any]:
        """记录一条实体知识三元组事实。

        Args:
            subject: 主体（如商品名称、店铺名、类目）。
            predicate: 属性关系（如 wholesale_min_price, main_supplier, defect_rate）。
            object_val: 属性值或事实内容。
            confidence: 置信度。
            source: 来源渠道。

        Returns:
            Dict[str, Any]: 知识事实字典。
        """
        facts = self.query_facts()
        entry = {
            "id": f"fact_{len(facts) + 1}",
            "subject": subject.strip(),
            "predicate": predicate.strip(),
            "object": object_val,
            "confidence": confidence,
            "source": source,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        # 若存在同主词同关系，则执行更新
        updated = False
        for f in facts:
            if f.get("subject") == entry["subject"] and f.get("predicate") == entry["predicate"]:
                f.update(entry)
                updated = True
                break
        if not updated:
            facts.append(entry)

        with open(self.facts_file, "w", encoding="utf-8") as f_out:
            json.dump(facts, f_out, ensure_ascii=False, indent=2)

        return entry

    def query_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询符合主体或关系条件的事实条目。

        Args:
            subject: 主体过滤词。
            predicate: 关系属性过滤词。

        Returns:
            List[Dict[str, Any]]: 匹配的事实列表。
        """
        try:
            with open(self.facts_file, "r", encoding="utf-8") as f:
                facts: List[Dict[str, Any]] = json.load(f)
        except Exception:
            return []

        results = facts
        if subject:
            results = [r for r in results if subject.lower() in r.get("subject", "").lower()]
        if predicate:
            results = [r for r in results if r.get("predicate") == predicate]
        return results

    def search(self, keyword: str) -> Dict[str, Any]:
        """全局模糊检索包含特定关键词的长效记忆与知识事实。

        Args:
            keyword: 检索词。

        Returns:
            Dict[str, Any]: 包含匹配 rules 与 facts 的综合字典。
        """
        kw = keyword.lower()
        matched_rules = [
            r for r in self.get_rules()
            if kw in r.get("text", "").lower() or kw in r.get("category", "").lower()
        ]
        matched_facts = [
            f for f in self.query_facts()
            if kw in f.get("subject", "").lower()
            or kw in str(f.get("object", "")).lower()
            or kw in f.get("predicate", "").lower()
        ]
        return {
            "rules": matched_rules,
            "facts": matched_facts,
        }
