"""阈值实证：相关/无关查询各 10 条，测 ANN top1 余弦距离分布，检验 0.45 的分离性。

诚实说明：相关=领域内应命中的问题；无关=领域外问题。样本小（10+10），
结论用于判断"距离阈值是否可作为相关性闸门"，正式上线前需更大标注集 + 随语料增长复测。
"""
import sys

sys.path.insert(0, "/home/sp/TaoZhi-EcomAgent/src")

from ecom_copilot.enterprise.db import database_connection, fetch_all
from ecom_copilot.enterprise.embedding import encode_query

RELEVANT = [
    "MC-500 膜切机切幅是多少",
    "钢化膜 C1 和高清膜有什么区别",
    "订单签收后几天内可以换新",
    "MagSafe 壳 T20 适配哪些机型",
    "陶瓷膜 C5 适合折叠屏吗",
    "客服处理售后换新的流程是什么",
    "水凝膜 C4 背膜 B1 的规格参数",
    "增值税专用发票税点多少",
    "新员工上岗培训考核内容",
    "私域客服话术标准",
]
IRRELEVANT = [
    "外星人飞船保修政策",
    "今天天气怎么样",
    "番茄炒蛋先放蛋还是先放番茄",
    "比特币今晚涨还是跌",
    "怎么养好一盆绿萝",
    "世界杯决赛几点开始",
    "Python 列表和字典的区别",
    "腰椎间盘突出怎么治疗",
    "二手车过户流程",
    "中考满分多少分",
]

with database_connection() as c:
    print(f"{'相关查询':<28} {'top1距离':>8}   命中文档")
    rel = []
    for q in RELEVANT:
        qv = encode_query(q)[0]
        r = fetch_all(c, """select round((c.embedding <=> %s::vector)::numeric,4) dist, d.title
            from chunks c join documents d on d.id=c.document_id and d.current_version=c.version
            where d.state='published' and c.embedding is not null
            order by c.embedding <=> %s::vector limit 1""", (qv, qv))[0]
        rel.append(float(r["dist"]))
        print(f"{q:<28} {r['dist']:>8}   {r['title'][:26]}")
    print()
    print(f"{'无关查询':<28} {'top1距离':>8}   命中文档")
    irr = []
    for q in IRRELEVANT:
        qv = encode_query(q)[0]
        r = fetch_all(c, """select round((c.embedding <=> %s::vector)::numeric,4) dist, d.title
            from chunks c join documents d on d.id=c.document_id and d.current_version=c.version
            where d.state='published' and c.embedding is not null
            order by c.embedding <=> %s::vector limit 1""", (qv, qv))[0]
        irr.append(float(r["dist"]))
        print(f"{q:<28} {r['dist']:>8}   {r['title'][:26]}")
    print()
    print(f"相关: min={min(rel):.4f} max={max(rel):.4f} 均值={sum(rel)/len(rel):.4f}")
    print(f"无关: min={min(irr):.4f} max={max(irr):.4f} 均值={sum(irr)/len(irr):.4f}")
    gap = min(irr) - max(rel)
    print(f"分离间隙: {gap:.4f}（无关最小值 - 相关最大值）→ {'完全分离，0.45 附近可取' if gap > 0 else '有重叠，单阈值会误伤'}")
