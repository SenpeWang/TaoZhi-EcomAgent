"""按租户存储调研事实和规则，通过文件锁串行化并发更新。"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional


@contextmanager
def _write_lock(path: Path) -> Iterator[None]:
    """读、改、替换共用一把稳定的文件锁，关闭描述符时释放。"""
    with path.with_suffix(".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read_entries(path: Path) -> list[dict]:
    """从已原子发布的版本读取记录；损坏的文件保留给运行日志诊断。"""
    with path.open(encoding="utf-8") as stream:
        entries = json.load(stream)
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("调研记忆文件格式无效")
    return entries


def _write_entries(path: Path, entries: list[dict]) -> None:
    """在同一目录完成序列化后替换旧版本，失败时清理未发布文件。"""
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(entries, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _next_id(prefix: str, entries: list[dict]) -> str:
    """为已有版本保留原编号，并避开历史记录中可能存在的非连续编号。"""
    used = {str(entry.get("id")) for entry in entries}
    number = len(entries) + 1
    while f"{prefix}_{number}" in used:
        number += 1
    return f"{prefix}_{number}"


class LongTermMemoryStore:
    """持久化长效记忆仓库，管理租户级规则、偏好设定与电商知识事实。"""

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        tenant_id: str = "default",
    ) -> None:
        """初始化指定租户的持久化记忆库。

        Args:
            storage_dir: 自定义存储根路径。
            tenant_id: 租户标识符，实现物理数据隔离。
        """
        self.tenant_id = str(tenant_id).strip() or "default"
        if self.tenant_id in {".", ".."} or Path(self.tenant_id).name != self.tenant_id:
            raise ValueError("租户标识必须为单个目录名")
        base_dir = Path(storage_dir) if storage_dir is not None else Path(
            os.environ.get("ECOM_MEMORY_DIR") or Path(__file__).resolve().parents[4] / "data/runtime/memory"
        )
        self.storage_dir = base_dir / self.tenant_id
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.rules_file = self.storage_dir / "learned_rules.json"
        self.facts_file = self.storage_dir / "entity_facts.json"

        for path in (self.rules_file, self.facts_file):
            if not path.exists():
                with _write_lock(path):
                    if not path.exists():
                        _write_entries(path, [])

    def learn_rule(
        self,
        rule_text: str,
        category: str = "general",
        source: str = "user_instruction",
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        """沉淀一条持久化学习规则。

        Args:
            rule_text: 规则具体表述。
            category: 类别（如 style, constraint, tool_policy, ecom_rule）。
            source: 来源说明。
            confidence: 置信度。

        Returns:
            Dict[str, Any]: 新增的规则条目。
        """
        text = rule_text.strip()
        with _write_lock(self.rules_file):
            rules = _read_entries(self.rules_file)
            for rule in rules:
                if rule.get("text") == text:
                    return rule
            entry = {
                "id": _next_id("rule", rules),
                "tenant_id": self.tenant_id,
                "text": text,
                "category": category,
                "source": source,
                "confidence": confidence,
                "learned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            rules.append(entry)
            _write_entries(self.rules_file, rules)
            return entry

    def get_rules(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取当前租户所有已学得的持久化规则。

        Args:
            category: 可选按类别筛选。

        Returns:
            List[Dict[str, Any]]: 规则列表。
        """
        items = _read_entries(self.rules_file)
        if category:
            return [it for it in items if it.get("category") == category]
        return items

    def remember_fact(
        self,
        subject: str,
        predicate: str,
        object_val: Any,
        confidence: float = 1.0,
        source: str = "agent_discovery",
        allow_overwrite: bool = True,
    ) -> Dict[str, Any]:
        """记录一条实体知识三元组事实。

        Args:
            subject: 主体（如商品名称、店铺名、类目）。
            predicate: 属性关系（如 wholesale_min_price, main_supplier, defect_rate）。
            object_val: 属性值或事实内容。
            confidence: 置信度。
            source: 来源渠道。
            allow_overwrite: 是否在主谓完全相同时允许覆盖。

        Returns:
            Dict[str, Any]: 知识事实字典。
        """
        subject, predicate = subject.strip(), predicate.strip()
        with _write_lock(self.facts_file):
            facts = _read_entries(self.facts_file)
            entry = next((
                fact for fact in facts
                if allow_overwrite and fact.get("subject") == subject and fact.get("predicate") == predicate
            ), None)
            if entry is None:
                entry = {"id": _next_id("fact", facts), "tenant_id": self.tenant_id,
                         "subject": subject, "predicate": predicate}
                facts.append(entry)
            entry.update(object=object_val, confidence=confidence, source=source,
                         updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            _write_entries(self.facts_file, facts)
            return entry

    def query_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询当前租户符合主体或关系条件的事实条目。

        Args:
            subject: 主体过滤词。
            predicate: 关系属性过滤词。

        Returns:
            List[Dict[str, Any]]: 匹配的事实列表。
        """
        results = _read_entries(self.facts_file)
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
