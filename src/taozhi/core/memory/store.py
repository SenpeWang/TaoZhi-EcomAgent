"""跨会话长效记忆与学习规则存储（Long-Term Memory Store）。

自研长效记忆存储架构：
1. 跨会话持久化沉淀已学习规则 (/learn)、用户偏好与业务实体事实；
2. 严格按租户 (tenant_id) 进行物理文件与目录隔离；
3. 动态追溯项目根路径，杜绝绝对路径硬编码；
4. 采用进程排他锁 (fcntl.flock) 与原子替换 (os.replace) 保障并发写入安全；
5. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def _resolve_default_storage_dir() -> Path:
    """动态定位长效记忆数据目录，杜绝绝对路径硬编码。

    Returns:
        Path: 解析出的记忆存储根目录路径。
    """
    env_dir = os.getenv("ECOM_MEMORY_DIR")
    if env_dir:
        return Path(env_dir)

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "STRUCTURE.md").exists() or (parent / "pyproject.toml").exists():
            return parent / "data" / "runtime" / "memory"

    return current.parents[4] / "data" / "runtime" / "memory"


def _atomic_write_json(file_path: Path, data: Any) -> None:
    """原子写入 JSON 文件并加进程排他锁，保障多进程并发安全与防损坏。

    Args:
        file_path: 目标 JSON 文件路径。
        data: 待序列化的数据结构。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = file_path.with_suffix(".lock")
    with open(lock_file, "a", encoding="utf-8") as lock_f:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            temp_fd, temp_path = tempfile.mkstemp(
                dir=str(file_path.parent),
                prefix=f".tmp_{file_path.stem}_",
                suffix=".json",
            )
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, str(file_path))
        finally:
            try:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
            except OSError:
                pass


def _safe_read_json(file_path: Path) -> List[Dict[str, Any]]:
    """安全读取 JSON 文件，加共享读锁并防御空文件或损坏。

    Args:
        file_path: 待读取的文件路径。

    Returns:
        List[Dict[str, Any]]: 反序列化后的列表。
    """
    if not file_path.exists():
        return []
    lock_file = file_path.with_suffix(".lock")
    try:
        with open(lock_file, "a", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_SH)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        return []
                    data = json.loads(content)
                    return data if isinstance(data, list) else []
            finally:
                try:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
                except OSError:
                    pass
    except Exception:
        return []


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
        base_dir = _resolve_default_storage_dir() if storage_dir is None else Path(storage_dir)
        self.storage_dir = base_dir / self.tenant_id
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.rules_file = self.storage_dir / "learned_rules.json"
        self.facts_file = self.storage_dir / "entity_facts.json"

        self._ensure_files()

    def _ensure_files(self) -> None:
        """确保存储文件存在。"""
        if not self.rules_file.exists():
            _atomic_write_json(self.rules_file, [])
        if not self.facts_file.exists():
            _atomic_write_json(self.facts_file, [])

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
        rules = self.get_rules()
        entry = {
            "id": f"rule_{len(rules) + 1}",
            "tenant_id": self.tenant_id,
            "text": rule_text.strip(),
            "category": category,
            "source": source,
            "confidence": confidence,
            "learned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if not any(r.get("text") == entry["text"] for r in rules):
            rules.append(entry)
            _atomic_write_json(self.rules_file, rules)
        return entry

    def get_rules(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取当前租户所有已学得的持久化规则。

        Args:
            category: 可选按类别筛选。

        Returns:
            List[Dict[str, Any]]: 规则列表。
        """
        items = _safe_read_json(self.rules_file)
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
        facts = self.query_facts()
        entry = {
            "id": f"fact_{len(facts) + 1}",
            "tenant_id": self.tenant_id,
            "subject": subject.strip(),
            "predicate": predicate.strip(),
            "object": object_val,
            "confidence": confidence,
            "source": source,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        updated = False
        if allow_overwrite:
            for f in facts:
                if f.get("subject") == entry["subject"] and f.get("predicate") == entry["predicate"]:
                    f.update(entry)
                    updated = True
                    break
        if not updated:
            facts.append(entry)

        _atomic_write_json(self.facts_file, facts)
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
        facts = _safe_read_json(self.facts_file)
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
