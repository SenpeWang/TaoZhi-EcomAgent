"""知识库问答领域核验规则：专家角色定义、逐字/省略号引文匹配与可溯源算式验证。"""
from __future__ import annotations

import ast
import re
from typing import Dict, Tuple

# 原有称谓参与意图匹配和向量索引。用户界面与执行记录的名称由 agent_catalog 解析。
SPECIALIST_AGENTS: Dict[str, Tuple[str, str]] = {
    "product": ("Spec 规格专家", "核对型号、尺寸、材料、批次和参数，不混用不同型号。"),
    "compatibility": ("Details 细节攻坚", "死磕手机完整机型、屏幕形态、微缝公差与互斥死角，不推测兼容。"),
    "after_sales": ("Support 售后支持", "核对渠道、凭证、时间及售后条件，不承诺退款、赔付。"),
    "coach": ("Coach 研学教练", "负责员工商品知识研习导学、设备操作规程带教与疑难排障教学。"),
    "operations": ("运营选品", "核对商品卖点和选品依据，不捏造销量、转化率、排名。"),
    "supply": ("库存供应链", "只使用当前业务快照与供应链资料，缺少接口时明确未接入。"),
    "finance": ("经营分析", "核对财务依据，不推测利润、采购价或客户经营信息。"),
}

_NUM_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)")
_VOLT_AMP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[Vv]\s*(\d+(?:\.\d+)?)\s*[Aa]")
_ARITH_UNIT_RE = re.compile(r"(?:个工作日|工作日|小时|元|块|折|成|天|台|件|%|W|w|g)")


def norm_text(s: object) -> str:
    """引用比对前的空白归一化：忽略换行、空格与全角空格差异。"""
    return "".join(str(s).split())


def quote_supported(quote: str, doc_quote: str) -> bool:
    """校验引用片段是否受原文支撑；支持跨行空白归一化与省略号有序锚点匹配。"""
    q_norm = norm_text(quote)
    d_norm = norm_text(doc_quote)
    if not q_norm or not d_norm:
        return False
    if q_norm in d_norm:
        return True
    if "..." not in q_norm and "…" not in q_norm:
        return False
    segs = [s for s in re.split(r"(?:\.{3,}|…+)", q_norm) if s]
    if not segs or not any(len(s) >= 4 for s in segs):
        return False
    cursor = 0
    for seg in segs:
        pos = d_norm.find(seg, cursor)
        if pos < 0:
            return False
        cursor = pos + len(seg)
    return True


def _eval_arith_ast(node: ast.AST, grounded_vals: set[float]) -> float | None:
    """安全求值四则运算 AST：仅当所有字面量操作数均在已核验集合中时返回结果。"""
    if isinstance(node, ast.Expression):
        return _eval_arith_ast(node.body, grounded_vals)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        val = float(node.value)
        return val if any(abs(val - gv) < 1e-6 for gv in grounded_vals) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sub = _eval_arith_ast(node.operand, grounded_vals)
        if sub is None:
            return None
        return sub if isinstance(node.op, ast.UAdd) else -sub
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _eval_arith_ast(node.left, grounded_vals)
        right = _eval_arith_ast(node.right, grounded_vals)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return None if abs(right) < 1e-9 else left / right
    return None


def numbers_supported(claim: str, allowed_doc_text: str, *, question: str = "", extra_doc_text: str = "") -> bool:
    """数字反幻觉核验：放行原文/问题已知数字及操作数全部可溯源的四则/比例算式结果。"""
    claim_nums = _NUM_RE.findall(claim)
    if not claim_nums:
        return True
    src_pool_text = f"{allowed_doc_text}\n{extra_doc_text}\n{question}"
    src_tokens = set(_NUM_RE.findall(src_pool_text))
    grounded_vals = {float(x) for x in src_tokens}
    for v_s, a_s in _VOLT_AMP_RE.findall(src_pool_text):
        v_val, a_val = float(v_s), float(a_s)
        grounded_vals.update({v_val, a_val, v_val * a_val})
    for a_s, b_s in _RATIO_RE.findall(f"{allowed_doc_text}\n{extra_doc_text}"):
        a_val, b_val = float(a_s), float(b_s)
        total = a_val + b_val
        if total > 0:
            grounded_vals.update(
                {total, a_val / total, b_val / total, 10.0 * a_val / total, 10.0 * b_val / total, 100.0 * a_val / total, 100.0 * b_val / total}
            )
    eq_parts = re.split(r"[=＝]", claim)
    for idx in range(len(eq_parts) - 1):
        lhs_raw = eq_parts[idx]
        m_rhs = re.match(r"\s*(\d+(?:\.\d+)?)", eq_parts[idx + 1])
        if not m_rhs:
            continue
        expr = lhs_raw.replace("×", "*").replace("÷", "/").replace("（", "(").replace("）", ")")
        expr = "".join(_ARITH_UNIT_RE.sub("", expr).split())
        m_tail = re.search(r"([(\d][0-9+\-*/().]*)$", expr)
        if not m_tail:
            continue
        expr = m_tail.group(1)
        if not re.search(r"[+\-*/]", expr):
            continue
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            continue
        val = _eval_arith_ast(tree, grounded_vals)
        if val is None:
            continue
        rhs_val = float(m_rhs.group(1))
        if abs(val - rhs_val) < 1e-4 or abs(val * 100.0 - rhs_val) < 1e-4:
            grounded_vals.add(rhs_val)
            grounded_vals.add(val)
    for tok in claim_nums:
        if tok in src_tokens:
            continue
        tv = float(tok)
        if not any(abs(tv - gv) < 1e-4 for gv in grounded_vals):
            return False
    return True
