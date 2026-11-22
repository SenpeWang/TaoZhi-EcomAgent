#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总各批次 source_index.jsonl 并用 hashlib 计算文件 sha256，生成统一 manifest.jsonl。

用法：python3 build_manifest.py
只读各交付目录文件并写一个 manifest，不修改其他内容。
"""
import hashlib
import json
import os

ROOT = "/home/quanyj/wsp-workspace/00_base/multi-agent/data"
BATCH_AT = "2026-10-07"
SOURCES = [
    ("incoming/待映射/products_official/apple/source_index.jsonl", "incoming/待映射/products_official/apple"),
    ("incoming/待映射/products_official/huawei/source_index.jsonl", "incoming/待映射/products_official/huawei"),
    ("incoming/待映射/products_official/thirdparty/source_index.jsonl", "incoming/待映射/products_official/thirdparty"),
    ("incoming/待映射/products_official/other_brands/source_index.jsonl", "incoming/待映射/products_official/other_brands"),
    ("corpus/policies/source_index.jsonl", "corpus/policies"),
]
EXTRA_FILES = []  # (relpath, category, source_name, suggested_grade, pending)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_extra():
    """登记没有 source_index 的交付物：台账模板、合成数据、接口样例外的生成物。"""
    out = []
    led = os.path.join(ROOT, "incoming/待映射/ledger_templates")
    for dirpath, _dirs, files in os.walk(led):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
            out.append((rel, "企业提供（空模板）", "字段口径模板", "组长级",
                        ["待企业提供真实导出"]))
    syn = os.path.join(ROOT, "corpus/synthetic")
    for dirpath, _dirs, files in os.walk(syn):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
            out.append((rel, "合成测试", "AI 生成（seed=20261007）", "组长级",
                        ["仅 demo/test，不得进入正式数据"]))
    ev = os.path.join(ROOT, "runtime/evaluation")
    for dirpath, _dirs, files in os.walk(ev):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
            out.append((rel, "问答评测集", "AI 基于已交付资料生成", "组长级",
                        ["用例答案依赖对应资料版本，资料更新后需同步修订用例"]))
    return out


def main():
    rows, missing = [], []
    for idx_path, base_dir in SOURCES:
        full = os.path.join(ROOT, idx_path)
        if not os.path.exists(full):
            missing.append(idx_path)
            continue
        with open(full, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                file_rel = os.path.normpath(os.path.join(os.path.dirname(idx_path), rec.get("file", "")))
                absf = os.path.join(ROOT, file_rel)
                if not os.path.exists(absf):
                    missing.append(file_rel)
                    continue
                rows.append({
                    "file": file_rel, "data_category": rec.get("category"),
                    "source_url": rec.get("source_url"), "source_name": rec.get("source_name"),
                    "collected_at": rec.get("collected_at", BATCH_AT), "region": rec.get("region"),
                    "model": rec.get("model"), "version": rec.get("version"),
                    "suggested_dept": rec.get("suggested_dept"), "suggested_group": rec.get("suggested_group"),
                    "suggested_grade": rec.get("suggested_grade"), "pending": rec.get("pending", []),
                    "sha256": sha256(absf), "bytes": os.path.getsize(absf),
                    "note": "建议密级仅为标签，实际权限必须在项目后台确认，不因文件内容自动生效",
                })
    for rel, cat, src, grade, pending in EXTRA_FILES + walk_extra():
        absf = os.path.join(ROOT, rel)
        if os.path.exists(absf):
            rows.append({"file": rel, "data_category": cat, "source_url": None, "source_name": src,
                         "collected_at": BATCH_AT, "region": None, "model": None, "version": None,
                         "suggested_dept": None, "suggested_group": None, "suggested_grade": grade,
                         "pending": pending, "sha256": sha256(absf), "bytes": os.path.getsize(absf),
                         "note": "建议密级仅为标签，实际权限必须在项目后台确认"})
    out_path = os.path.join(ROOT, "incoming/待映射/manifest.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"manifest 行数: {len(rows)} -> {out_path}")
    if missing:
        print("缺失/未登记:", *missing, sep="\n  ")
    by_cat = {}
    for r in rows:
        by_cat[r["data_category"]] = by_cat.get(r["data_category"], 0) + 1
    print("分类统计:", json.dumps(by_cat, ensure_ascii=False))


if __name__ == "__main__":
    main()
