#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁：把 evals/run_evals.py 的来源解析改为「路径后缀唯一匹配」。

背景（2026-10-17 审核发现）：
1. 用例的 source_files 记录的是**交付时**路径（incoming/待映射/...），目录后来改名为 mofa，
   按整条路径拼接失配 → 检索池可评估覆盖率只剩 22%，而指标照样算得出来（最危险的静默失效）。
2. 按「文件名」建索引也不行：products.jsonl / compatibility.jsonl / source_index.jsonl 在多个
   品牌目录里同名（4~5 份），会把 huawei 的题解析到 apple 的文档上——拿错品牌的参数打分，
   比解析失败更隐蔽。
3. 正确做法：按**尾路径唯一匹配**（品牌目录也参与），歧义即视为未解析，宁缺勿错。

用法（在 WSL 里、以项目根目录为工作目录执行）：
    python evals/tools/apply_resolver_fix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "evals/run_evals.py"

START_MARK = "# ─────────────── 检索质量层 ───────────────"
END_MARK = "\n\ndef phase_retrieval(sample=60):"

NEW = '''# ─────────────── 检索质量层 ───────────────
def title_of(p):
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
    return p.stem


# 交付路径的陈旧前缀（目录改名/搬迁后的历史写法），解析时逐个剥离后按尾路径匹配。
_STALE_PREFIX = ("data/", "incoming/待映射/", "incoming/mofa/", "corpus/", "incoming/")
_CORPUS = None


def _build_corpus():
    """扫描语料根目录，建立「尾路径 -> 文件集合」索引。

    只扫 data/corpus 与 data/incoming，不碰私有目录与运行时目录。
    """
    files = []
    for base in ("data/corpus", "data/incoming"):
        root = ROOT / base
        if root.is_dir():
            files += [q for q in root.rglob("*")
                      if q.is_file() and (q.suffix == ".md" or q.name.endswith(".jsonl"))]
    suffix = {}
    for f in files:
        parts = f.relative_to(ROOT).as_posix().split("/")
        for k in range(1, min(5, len(parts) + 1)):
            suffix.setdefault("/".join(parts[-k:]), set()).add(f)
    return files, suffix


def corpus_index():
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = _build_corpus()
    return _CORPUS


def resolve_source(file_field):
    """把用例里的来源路径解析为磁盘上的实际文件；解析不到或存在歧义时返回 None。

    顺序：原始拼法 → 剥离陈旧前缀后按尾路径唯一匹配。歧义即放弃（不猜品牌）。
    """
    if not file_field:
        return None
    for cand in (ROOT / "data" / file_field,
                 ROOT / "data/incoming/待映射" / file_field,
                 ROOT / "data/incoming/mofa" / file_field,
                 ROOT / file_field):
        if cand.exists():
            return cand
    text = file_field
    for prefix in _STALE_PREFIX:
        if text.startswith(prefix):
            text = text[len(prefix):]
    parts = [p for p in text.split("/") if p]
    _, suffix = corpus_index()
    for k in range(len(parts), 0, -1):
        hits = suffix.get("/".join(parts[-k:]))
        if hits and len(hits) == 1:
            return next(iter(hits))
    return None


def expected_titles(case):
    """用例的预期文档标题集合（命中判定用）。"""
    return _expected(case)[0]


def _expected(case):
    """返回 (预期标题集合, 未解析的来源文件名列表)。"""
    out, unresolved = set(), []
    for sf in case.get("source_files") or []:
        f = sf.get("file") or ""
        target = resolve_source(f)
        if target is None:
            unresolved.append(f)
            continue
        if target.suffix == ".md":
            out.add(title_of(target))
        elif target.name.endswith(".jsonl"):
            for md in sorted(target.parent.glob("*.md")):
                out.add(title_of(md))
    return out, unresolved
'''


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    if "_build_corpus" in src:
        print(f"已打过补丁，跳过：{TARGET}")
        return 0
    i = src.find(START_MARK)
    j = src.find(END_MARK)
    if i < 0 or j <= i:
        print("未定位到待替换片段（标记缺失），请人工核对 run_evals.py", file=sys.stderr)
        return 1
    TARGET.write_text(src[:i] + NEW + src[j:], encoding="utf-8")
    print(f"已写入：{TARGET}")
    print("  解析规则：原始拼法 → 剥离陈旧前缀 → 尾路径唯一匹配（歧义即放弃）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
