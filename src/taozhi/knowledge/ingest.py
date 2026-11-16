"""文档入库校验、统一文本抽取与结构感知切片。"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from typing import List, Optional, Tuple

from taozhi.domain.errors import Denied

_FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)


def validate_upload(name: str, data: bytes) -> str:
    """校验上传附件大小、扩展名与文件魔数，返回 MIME 类型。"""
    if not data or len(data) > 20 * 1024 * 1024:
        raise Denied("附件大小须为 1 字节至 20 MB", "UPLOAD_SIZE", 400)
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "pdf":
        if not data.startswith(b"%PDF-"):
            raise Denied("PDF 格式无效", "UPLOAD_FORMAT", 400)
        return "application/pdf"
    if suffix == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if (
                    "word/document.xml" not in z.namelist()
                    or sum(x.file_size for x in z.infolist()) > 30 * 1024 * 1024
                    or any(x.filename.startswith("/") or ".." in x.filename.split("/") for x in z.infolist())
                ):
                    raise ValueError()
        except Exception:
            raise Denied("文档格式无效或解压内容过大", "UPLOAD_FORMAT", 400) from None
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix in ("txt", "md"):
        try:
            data.decode("utf-8-sig")
        except UnicodeError:
            raise Denied("文本附件须为 UTF-8 编码", "UPLOAD_ENCODING", 400) from None
        return "text/plain"
    raise Denied("支持 PDF、Word、文本和 Markdown 附件", "UPLOAD_FORMAT", 400)


def extract_pdf_pages_bytes(data: bytes, enforce_limits: bool = True) -> List[Tuple[int, str]]:
    """使用 PyMuPDF 按页提取 PDF 文本，返回 [(1-based 页码, 文本), ...]。"""
    import fitz

    with fitz.open(stream=data, filetype="pdf") as pdf:
        if enforce_limits and (pdf.is_encrypted or len(pdf) > 200):
            raise Denied("暂不支持加密或超过 200 页的 PDF", "PARSE_LIMIT", 400)
        return [(i + 1, pdf[i].get_text("text")) for i in range(len(pdf))]


def extract_docx_text_bytes(data: bytes, table_sep: str = " | ") -> str:
    """提取 DOCX 正文、页眉/页脚与表格文本。

    页眉/页脚常携带制度版本号、备案号与生效日期；与语料读取共用本实现，
    消除生产上传漏读页眉与评测测试走不同路径产生的假绿差异。
    """
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for section in doc.sections:
        parts.extend(p.text for p in section.header.paragraphs)
        parts.extend(p.text for p in section.footer.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            parts.append(table_sep.join(cell.text.strip() for cell in row.cells))
    return "\n".join(x for x in parts if x.strip())


def parse(data: bytes, mime: str) -> Tuple[str, List[Tuple[int, str]]]:
    """解析上传文档字节流为 (合并正文, 按页列表)。"""
    if mime == "application/pdf":
        pages = extract_pdf_pages_bytes(data, enforce_limits=True)
    elif "wordprocessingml" in mime:
        pages = [(0, extract_docx_text_bytes(data, table_sep=" | "))]
    else:
        pages = [(0, data.decode("utf-8-sig"))]
    body = "\n\n".join(text for _, text in pages)
    if not body.strip() or len(body) > 200000:
        raise Denied("未提取到可读文本或内容超过限制", "PARSE_LIMIT", 400)
    return body, pages


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
            # 超长单行再按句界切分；仍超长才做字符兜底切分
            sents = re.split(r"(?<=[。！？；\.\!\?;])\s*", line)
            for s in sents:
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


def _chunk_limits() -> Tuple[int, int]:
    """从运行时配置读取切片尺寸与重叠；单测无库环境回落 Config 类定义默认值。"""
    from ..config import Config, load_config

    try:
        cfg = load_config()
        return max(128, int(cfg.chunk_size)), max(0, int(cfg.chunk_overlap))
    except Exception:
        return int(Config.chunk_size), int(Config.chunk_overlap)


def split(
    pages: List[Tuple[int, str]],
    doc_id: str,
    version: int,
    max_chars: Optional[int] = None,
    overlap: Optional[int] = None,
) -> List[dict]:
    """按段落/表格/代码块边界聚合切片，并保留相邻块整句重叠，避免在表格或行中硬截断。"""
    default_size, default_overlap = _chunk_limits()
    limit = max(128, max_chars if max_chars is not None else default_size)
    overlap_budget = max(0, min(limit // 2, overlap if overlap is not None else default_overlap))

    result: List[dict] = []
    for page, raw_text in pages:
        text = (raw_text or "").strip()
        if not text:
            continue
        blocks = _structural_blocks(text, limit)
        buf: List[str] = []
        buf_len = 0

        def _flush() -> None:
            nonlocal buf, buf_len
            joined = "\n\n".join(buf).strip()
            if not joined:
                buf, buf_len = [], 0
                return
            cid = "chunk_" + hashlib.sha256(f"{doc_id}:{version}:{len(result)}".encode()).hexdigest()[:24]
            result.append(dict(id=cid, ordinal=len(result), page=page, text=joined))
            if overlap_budget <= 0:
                buf, buf_len = [], 0
                return
            # 按完整块/行做重叠尾部继承，不从字词中间劈开
            carry: List[str] = []
            carry_len = 0
            for prev_blk in reversed(buf):
                if carry_len + len(prev_blk) <= overlap_budget:
                    carry.insert(0, prev_blk)
                    carry_len += len(prev_blk) + (2 if len(carry) > 1 else 0)
                else:
                    break
            buf = carry
            buf_len = sum(len(x) for x in buf) + max(0, len(buf) - 1) * 2

        for blk in blocks:
            added = len(blk) + (2 if buf else 0)
            if buf and buf_len + added > limit:
                _flush()
            # 防止重叠后的单条块仍超出上限
            while buf and buf_len + len(blk) + 2 > limit:
                buf.pop(0)
                buf_len = sum(len(x) for x in buf) + max(0, len(buf) - 1) * 2
            buf.append(blk)
            buf_len += len(blk) + (2 if len(buf) > 1 else 0)
        if buf:
            # 避免尾部仅仅是上一块已完整包含的纯重叠内容
            tail = "\n\n".join(buf).strip()
            if tail and (not result or tail not in result[-1]["text"]):
                cid = "chunk_" + hashlib.sha256(f"{doc_id}:{version}:{len(result)}".encode()).hexdigest()[:24]
                result.append(dict(id=cid, ordinal=len(result), page=page, text=tail))
    return result
