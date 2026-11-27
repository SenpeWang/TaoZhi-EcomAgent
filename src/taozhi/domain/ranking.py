"""文档切分、分词、版本时效与多实体多样性排序纯领域算法（无配置/IO依赖）。"""
from __future__ import annotations

import hashlib
import re
from collections import deque
from collections.abc import Iterator
from typing import Any, Dict, List, Optional, Tuple

from .entity_extraction import extract_entities

RRF_K = 60

_FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)
_HISTORY_QUERY_RE = re.compile(r"什么时候|以前|旧版|历史|差异|区别|变更|调整|改了")
_VERSION_TAG_RE = re.compile(r"(?<![A-Za-z0-9])V([0-9]{1,9}(?:\.[0-9]{1,9})*)(?![A-Za-z0-9])", re.IGNORECASE)
_DATE_TAG_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_BASE_TITLE_STRIP_RE = re.compile(r"(?:V\d+(?:\.\d+)*|\d{4}-\d{2}-\d{2}|（[^）]*）|\([^\)]*\)|\s+)", re.IGNORECASE)


def tokenize_zh(text: str) -> List[str]:
    """中文分词并返回规范化词条列表。"""
    import jieba

    return [x.casefold() for x in jieba.lcut(text) if x.strip() and (len(x) > 1 or x.isalnum())]


def _split_long_block(block: str, max_chars: int) -> List[str]:
    """对超出上限的单块按换行或中文句末标点细分，尽量不截断完整行或句子。"""
    if len(block) <= max_chars:
        return [block]
    units: List[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= max_chars:
            units.append(line)
        else:
            for s in re.split(r"(?<=[。！？；\.\!\?;])\s*", line):
                s = s.strip()
                if not s:
                    continue
                if len(s) <= max_chars:
                    units.append(s)
                else:
                    for pos in range(0, len(s), max_chars):
                        units.append(s[pos : pos + max_chars])
    packed: List[str] = []
    cur = ""
    for u in units:
        sep = "\n" if cur else ""
        if cur and len(cur) + len(sep) + len(u) > max_chars:
            packed.append(cur)
            cur = u
        else:
            cur = cur + sep + u
    if cur:
        packed.append(cur)
    return packed


def _structural_blocks(text: str, max_chars: int) -> List[str]:
    """保持代码块、表格块与段落边界完整地拆出基础语义单元。"""
    blocks: List[str] = []
    for part in _FENCE_RE.split(text):
        part = part.strip()
        if not part:
            continue
        if part.startswith("```") and part.endswith("```"):
            blocks.extend(_split_long_block(part, max_chars))
            continue
        for para in re.split(r"\n{2,}", part):
            para = para.strip()
            if para:
                blocks.extend(_split_long_block(para, max_chars))
    return blocks


def _page_chunks(text: str, limit: int, overlap: int) -> Iterator[str]:
    """在页内聚合完整段落，并为下一块保留预算范围内的结尾段落。"""
    blocks: deque[str] = deque()
    size = 0
    for block in _structural_blocks(text, limit):
        if blocks and size + 2 + len(block) > limit:
            yield "\n\n".join(blocks)
            carry_limit = min(overlap, limit - len(block) - 2)
            while blocks and size > carry_limit:
                size -= len(blocks.popleft())
                if blocks:
                    size -= 2
        size += len(block) + (2 if blocks else 0)
        blocks.append(block)
    if blocks:
        yield "\n\n".join(blocks)


def split_document_chunks(
    pages: List[Tuple[int, str]],
    doc_id: str,
    version: int,
    max_chars: int,
    overlap: int,
) -> List[dict]:
    """按段落/表格/代码块边界聚合切片，并保留相邻块整句重叠。"""
    limit = max(128, int(max_chars))
    overlap_budget = max(0, min(limit // 2, int(overlap)))

    result: List[dict] = []
    for page, raw_text in pages:
        for text in _page_chunks((raw_text or "").strip(), limit, overlap_budget):
            ordinal = len(result)
            cid = "chunk_" + hashlib.sha256(f"{doc_id}:{version}:{ordinal}".encode()).hexdigest()[:24]
            result.append(dict(id=cid, ordinal=ordinal, page=page, text=text))
    return result


def norm_chunk_key(text: str) -> str:
    """正文归一化指纹：去空白后取前 200 字，用于识别重复切片。"""
    return re.sub(r"\s+", "", text)[:200]


def fuse_evidence_rounds(first: list[dict], second: list[dict], limit: int) -> list[dict]:
    """按两轮名次的倒数和合并证据，同分优先首轮，保留原始引用与权限元数据。"""
    scores: dict[str, float] = {}
    documents: dict[str, dict] = {}
    for ranked in (first, second):
        seen = set()
        for rank, document in enumerate(ranked, start=1):
            chunk_id = document["chunk_id"]
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            documents.setdefault(chunk_id, document)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    ranked_ids = sorted(scores, key=scores.__getitem__, reverse=True)
    return [documents[chunk_id] for chunk_id in ranked_ids[:limit]]


def dedup_chunks_by_text(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """文本级去重：归一化指纹相同的切片只保留排序靠前的第一条。"""
    seen = set()
    out: List[Dict[str, Any]] = []
    for c in chunks:
        key = norm_chunk_key(c["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def chunk_title(chunk: Dict[str, Any], allowed: Optional[Dict[str, Any]] = None) -> str:
    """读取切片所属文档标题。"""
    if chunk.get("doc_title"):
        return str(chunk["doc_title"])
    if allowed and chunk.get("document_id") in allowed:
        return str(allowed[chunk["document_id"]].get("title") or "")
    return ""


def version_tuple(title: str, text: str) -> tuple:
    """提取文档版本与生效日期元信息：(major_ver, date_str)。"""
    probe = f"{title}\n{text[:200]}"
    versions = [tuple(int(part) for part in match.split(".")) for match in _VERSION_TAG_RE.findall(probe)]
    dates = _DATE_TAG_RE.findall(probe)
    return (max(versions, default=()), max(dates) if dates else "")


def apply_recency_tiebreak(query: str, ranked: list, chunks: list, allowed: dict, boost: float) -> list:
    """非历史对比问句下，同系列多版本文档温和提升最新版本权重。"""
    if boost <= 0 or len(ranked) < 2 or _HISTORY_QUERY_RE.search(query):
        return ranked
    families: Dict[str, Dict[str, tuple]] = {}
    for i, _ in ranked:
        c = chunks[int(i)]
        title = chunk_title(c, allowed)
        base = _BASE_TITLE_STRIP_RE.sub("", title)
        if len(base) < 2:
            continue
        ver = version_tuple(title, c["text"])
        if ver != ((), ""):
            families.setdefault(base, {})[c["document_id"]] = ver

    multi_ver_docs: Dict[str, float] = {}
    for _, doc_vers in families.items():
        unique_vers = set(doc_vers.values())
        if len(unique_vers) < 2:
            continue
        latest = max(unique_vers)
        for did, v in doc_vers.items():
            multi_ver_docs[did] = boost if v == latest else -boost

    if not multi_ver_docs:
        return ranked

    def _sort_key(pos_item):
        """计算多版本偏好重排的排序键。

        Args:
            pos_item: （原始位置，(切片下标, 分数)）二元组。

        Returns:
            含版本偏好加权与原始位置倒序的排序键。
        """
        pos, (idx, score) = pos_item
        base_score = score if score is not None else float(len(ranked) - pos) * 0.01
        did = chunks[int(idx)]["document_id"]
        return (base_score + multi_ver_docs.get(did, 0.0), -pos)

    reordered = sorted(enumerate(ranked), key=_sort_key, reverse=True)
    return [item for _, item in reordered]


def apply_entity_diversity(
    query: str,
    ranked: list,
    chunks: list,
    allowed: dict,
    limit: int,
    max_per_doc: int,
    entity_quota: int,
) -> list:
    """多实体对比问句下，先保底挑选覆盖不同提及实体的头部候选。"""
    entities = extract_entities(query)
    if len(entities) < 2 or entity_quota <= 0 or len(ranked) <= 1:
        return ranked

    selected = []
    selected_indices = set()
    per_doc: Dict[str, int] = {}
    for ent in entities:
        ent_u = ent.upper()
        picked = False
        for title_only in (True, False):
            for idx, score in ranked:
                if idx in selected_indices:
                    continue
                c = chunks[int(idx)]
                did = c["document_id"]
                if did not in allowed or per_doc.get(did, 0) >= max_per_doc:
                    continue
                title_u = chunk_title(c, allowed).upper()
                matched = ent_u in title_u if title_only else ent_u in f"{title_u}\n{c['text']}".upper()
                if matched:
                    selected.append((idx, score))
                    selected_indices.add(idx)
                    per_doc[did] = per_doc.get(did, 0) + 1
                    picked = True
                    break
            if picked:
                break
        if len(selected) >= min(limit, len(entities) * entity_quota):
            break

    for item in ranked:
        if item[0] not in selected_indices:
            selected.append(item)
    return selected
