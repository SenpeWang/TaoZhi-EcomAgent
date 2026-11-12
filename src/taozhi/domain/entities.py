# 领域实体定义：不依赖任何外层框架
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Principal:
    id: str
    tenant_id: str
    username: str
    display_name: str
    system_admin: bool
    version: int
    must_change: bool
    memberships: list
    nodes: dict

    def covers(self, a, b):
        seen = set()
        while b and b not in seen:
            if a == b:
                return True
            seen.add(b)
            b = self.nodes.get(b, {}).get("parent_id")
        return False

    def overlaps(self, a, b):
        return self.covers(a, b) or self.covers(b, a)

    @property
    def boss(self):
        return any(m["role"] == "boss" and self.nodes.get(m["node_id"], {}).get("kind") == "company" for m in self.memberships)

    @property
    def leader(self):
        return any(m["role"] == "leader" for m in self.memberships)

    @property
    def root(self):
        return next((n["id"] for n in self.nodes.values() if n["kind"] == "company"), "")

    def manages(self, node):
        return self.boss or any(m["role"] == "leader" and self.covers(m["node_id"], node) for m in self.memberships)

    def grade(self, node, scope="org"):
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

    def public(self):
        labels = []
        if self.system_admin:
            labels.append("管理员")
        if self.boss:
            labels.append("老板")
        elif self.leader:
            labels.append("组长")
        elif self.memberships or not self.system_admin:
            labels.append("员工")
        return dict(
            id=self.id,
            username=self.username,
            display_name=self.display_name,
            system_admin=self.system_admin,
            is_boss=self.boss,
            is_leader=self.leader,
            roles=labels,
            must_change=self.must_change,
            version=self.version,
            memberships=[dict(**m, name=self.nodes.get(m["node_id"], {}).get("name", "")) for m in self.memberships],
            capabilities=dict(
                system=self.system_admin or self.boss,
                write=self.system_admin or self.leader or self.boss,
                review=self.leader or self.boss,
                permissions=self.system_admin or self.leader or self.boss,
            ),
        )
