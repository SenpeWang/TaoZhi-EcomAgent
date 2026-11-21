"""领域核心主体实体定义（零外部框架依赖）。"""
from __future__ import annotations

from dataclasses import dataclass
from .roles import is_system_admin


@dataclass
class Principal:
    """经过会话鉴权的企业用户主体与组织树权限上下文。"""

    id: str
    tenant_id: str
    username: str
    display_name: str
    role_level: int
    version: int
    must_change: bool
    memberships: list
    nodes: dict

    def covers(self, a: str, b: str) -> bool:
        """判断组织节点 ``a`` 是否为节点 ``b`` 本身或上级节点。"""
        seen = set()
        while b and b not in seen:
            if a == b:
                return True
            seen.add(b)
            b = self.nodes.get(b, {}).get("parent_id")
        return False

    def overlaps(self, a: str, b: str) -> bool:
        """判断组织节点 ``a`` 与 ``b`` 是否在组织树上存在上下级重叠。"""
        return self.covers(a, b) or self.covers(b, a)

    @property
    def boss(self) -> bool:
        """是否拥有公司根节点老板角色。"""
        return any(
            m["role"] == "boss" and self.nodes.get(m["node_id"], {}).get("kind") == "company"
            for m in self.memberships
        )

    @property
    def leader(self) -> bool:
        """是否担任任意部门或小组负责人角色。"""
        return any(m["role"] == "leader" for m in self.memberships)

    @property
    def root(self) -> str:
        """返回所属租户公司根节点 ID。"""
        return next((n["id"] for n in self.nodes.values() if n["kind"] == "company"), "")

    def manages(self, node: str) -> bool:
        """判断主体是否管理目标组织节点。"""
        return self.boss or any(m["role"] == "leader" and self.covers(m["node_id"], node) for m in self.memberships)

    def grade(self, node: str, scope: str = "org") -> int:
        """计算主体针对指定组织范围与可见范围的最高数据密级上限（1-3）。"""
        if self.boss:
            return 3
        if scope == "company":
            return 2 if self.leader else 1
        levels = [
            2 if m["role"] == "leader" else 1
            for m in self.memberships
            if self.overlaps(m["node_id"], node)
            and not (m["role"] == "employee" and self.nodes.get(m["node_id"], {}).get("kind") == "company" and node != m["node_id"])
        ]
        return max(levels, default=0)

    def public(self) -> dict:
        """生成可安全返回前端的公开主体视图。"""
        labels = []
        if is_system_admin(self.role_level):
            labels.append("管理员")
        if self.boss:
            labels.append("老板")
        elif self.leader:
            labels.append("组长")
        elif self.memberships or not is_system_admin(self.role_level):
            labels.append("员工")
        return dict(
            id=self.id,
            username=self.username,
            display_name=self.display_name,
            role_level=self.role_level,
            is_boss=self.boss,
            is_leader=self.leader,
            roles=labels,
            must_change=self.must_change,
            version=self.version,
            memberships=[dict(**m, name=self.nodes.get(m["node_id"], {}).get("name", "")) for m in self.memberships],
            capabilities=dict(
                system=is_system_admin(self.role_level) or self.boss,
                write=is_system_admin(self.role_level) or self.leader or self.boss,
                review=self.leader or self.boss,
                permissions=is_system_admin(self.role_level) or self.leader or self.boss,
            ),
        )
