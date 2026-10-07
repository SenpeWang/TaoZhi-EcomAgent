"""从 data/corpus/ 下的企业知识语料批量渲染 8 份企业 PDF（data/corpus/pdf/）。

轻量 Markdown→PDF 渲染器：基于 PyMuPDF（fitz）内置 CJK 字体 china-ss。
支持 #~#### 标题、段落（CJK 逐字换行）、无序/有序列表、| 表格 |（自适应
列宽 + 网格线 + 跨页重复表头 + 超宽单元格截断加省略号）、引用块与分隔线；
代码块/图片不支持，遇到按普通段落处理。一份 PDF 可合并多篇 md，篇与篇
之间插入分隔页（注明来源文档名与文档编号）。

运行（语料更新后可重复执行，幂等覆盖旧 PDF）：
    .venv-v3/bin/python scripts/make_pdfs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus"
OUT_DIR = CORPUS / "pdf"

PAGE_W, PAGE_H = 595.0, 842.0  # A4
MARGIN = 46.0
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_TEXT = "膜法工坊 MofaLab · 电商商品知识智能问答系统演示语料"

INK = (0.12, 0.12, 0.12)
HEAD_COLOR = (0.05, 0.22, 0.50)
GRID_COLOR = (0.62, 0.62, 0.62)
HEADER_BG = (0.89, 0.93, 0.99)

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------

FONT_CANDIDATES = ["china-ss", "china-s"]
SYS_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
]


def _pick_fontname() -> str:
    for name in FONT_CANDIDATES:
        try:
            font = fitz.Font(name)
            if font.glyph_count > 1000:
                return name
        except Exception:
            continue
    for path in SYS_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return "china-ss"


FONTNAME = _pick_fontname()
if FONTNAME in FONT_CANDIDATES:
    FONT = fitz.Font(FONTNAME)
else:
    FONT = fitz.Font(fontfile=FONTNAME)

# china-ss 缺少部分字形（如 ✔/✘），渲染前做映射
_GLYPH_MAP = {"✔": "√", "✓": "√", "☑": "√", "✘": "×", "✗": "×",
              "☐": "□", "▪": "·", "◦": "·", "‐": "-", "‑": "-"}

_W_CACHE: dict[tuple[str, float], float] = {}


def _char_w(ch: str, size: float) -> float:
    key = (ch, size)
    w = _W_CACHE.get(key)
    if w is None:
        w = FONT.text_length(ch, fontsize=size)
        _W_CACHE[key] = w
    return w


def _text_w(text: str, size: float) -> float:
    return sum(_char_w(ch, size) for ch in text)


# 避免行首出现的标点（简易避头尾：允许轻微超宽）
_NO_LINE_START = set("，。、；：？！」』）】》〉%％℃°…!?,.;:)]}%\"'")


def _wrap(text: str, size: float, max_w: float) -> list[str]:
    """CJK 逐字符贪心换行，标点避头尾。"""
    lines: list[str] = []
    cur, cur_w = "", 0.0
    for ch in text:
        w = _char_w(ch, size)
        if cur and cur_w + w > max_w and ch not in _NO_LINE_START:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur += ch
            cur_w += w
    if cur:
        lines.append(cur)
    return lines or [""]


def _sanitize(text: str) -> str:
    out = []
    for ch in text:
        if ch in _GLYPH_MAP:
            ch = _GLYPH_MAP[ch]
        elif ord(ch) > 0xFFFF:
            continue  # emoji 等非 BMP 字符丢弃
        out.append(ch)
    return "".join(out)


def _strip_inline(s: str) -> str:
    """去掉行内标记（加粗/行内码/链接/图片），保留文字。"""
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    return s.strip()


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
LIST_RE = re.compile(r"^(\s*)(?:([-*+])|(\d{1,3})[.、)])\s+(.*)$")
HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
META_KEYS = ("文档编号", "版本", "生效日期", "归口部门", "保密等级")


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in TABLE_ROW_RE.match(line).group(1).split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", c) or not c for c in cells)


def _parse_head(lines: list[str]) -> tuple[dict[str, str], str | None]:
    """提取首篇元信息（文档编号等）与一级标题。"""
    meta: dict[str, str] = {}
    h1: str | None = None
    header: list[str] | None = None
    for line in lines[:20]:
        s = line.strip()
        if h1 is None and s.startswith("# "):
            h1 = s[2:].strip()
        if s.startswith("|"):
            cells = _split_row(line)
            if _is_separator_row(cells):
                continue
            if header is None and "文档编号" in cells:
                header = cells
                continue
            if header:
                for k, v in zip(header, cells):
                    if k in META_KEYS:
                        meta[k] = v
                break  # 只要首行数据
        elif s.startswith("-"):
            break
    return meta, h1


def _iter_blocks(lines: list[str]):
    """把 md 行解析为块序列：h / para / table / hr / quote / li。"""
    i, fence = 0, False
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if fence:
            if line.strip().startswith("```"):
                fence = False
            elif line.strip():
                yield ("para", line.strip())
            i += 1
            continue
        if line.strip().startswith("```"):
            fence = True  # 代码块：内容按段落渲染
            i += 1
            continue
        if not line.strip():
            i += 1
            continue
        m = HEADING_RE.match(line)
        if m:
            yield ("h", len(m.group(1)), m.group(2).strip())
            i += 1
            continue
        if TABLE_ROW_RE.match(line):
            header = _split_row(line)
            i += 1
            rows: list[list[str]] = []
            while i < len(lines) and TABLE_ROW_RE.match(lines[i]):
                cells = _split_row(lines[i])
                if not _is_separator_row(cells):
                    rows.append(cells)
                i += 1
            yield ("table", header, rows)
            continue
        if HR_RE.match(line):
            yield ("hr",)
            i += 1
            continue
        if line.lstrip().startswith(">"):
            yield ("quote", line.lstrip().lstrip(">").strip())
            i += 1
            continue
        lm = LIST_RE.match(raw)
        if lm:
            level = min(len(lm.group(1).expandtabs(2)) // 2, 3)
            yield ("li", level, lm.group(3), lm.group(4))
            i += 1
            continue
        yield ("para", line.strip())
        i += 1


# ---------------------------------------------------------------------------
# PDF 排版
# ---------------------------------------------------------------------------

class _Doc:
    """极简排版器：维护当前页与 y 游标，自动分页。"""

    def __init__(self, title: str) -> None:
        self.doc = fitz.open()
        self.title = title
        self.page: fitz.Page | None = None  # 首页由分隔页创建，避免空白首两页
        self.y = MARGIN

    # -- 基础 ---------------------------------------------------------------
    def _new_page(self) -> fitz.Page:
        return self.doc.new_page(width=PAGE_W, height=PAGE_H)

    def _ensure(self, need: float) -> bool:
        """空间不足则换页，返回是否发生了换页。"""
        if self.y + need <= PAGE_H - MARGIN:
            return False
        self.page = self._new_page()
        self.y = MARGIN
        return True

    def _put(self, x: float, y: float, text: str, size: float, color=INK) -> None:
        self.page.insert_text((x, y), _sanitize(text), fontsize=size,
                              fontname=FONTNAME, color=color)

    def _hline(self, color=GRID_COLOR, width: float = 0.6, pad_top: float = 4.0) -> None:
        self._ensure(10)
        shape = self.page.new_shape()
        shape.draw_line((MARGIN, self.y + pad_top), (PAGE_W - MARGIN, self.y + pad_top))
        shape.finish(color=color, width=width)
        shape.commit()
        self.y += pad_top + 5

    # -- 块级元素 -------------------------------------------------------------
    def heading(self, text: str, level: int) -> None:
        sizes = {1: 16.5, 2: 13.5, 3: 11.5, 4: 10.5}
        size = sizes.get(level, 10.0)
        self.y += 7 if level > 1 else 2
        self._ensure(size * 3.4)  # 标题尽量不落在页底
        for ln in _wrap(_strip_inline(text), size, CONTENT_W):
            self._put(MARGIN, self.y + size, ln, size,
                      HEAD_COLOR if level <= 2 else (0.10, 0.10, 0.15))
            self.y += size * 1.45
        if level <= 2:
            self.y += 2
            self._hline()

    def para(self, text: str, size: float = 9.8, x: float = MARGIN,
             color=INK, gap_after: float = 4.0) -> None:
        lh = size * 1.5
        for ln in _wrap(text, size, CONTENT_W - (x - MARGIN)):
            self._ensure(lh)
            self._put(x, self.y + size, ln, size, color)
            self.y += lh
        self.y += gap_after

    def list_item(self, level: int, num: str | None, text: str) -> None:
        size = 9.8
        lh = size * 1.5
        x = MARGIN + 6 + min(level, 3) * 14
        tx = x + 14
        lines = _wrap(_strip_inline(text), size, CONTENT_W - (tx - MARGIN))
        need = len(lines) * lh + 2
        if need < PAGE_H - 2 * MARGIN:
            self._ensure(need)
        marker = f"{num}." if num else "•"
        first = True
        for ln in lines:
            self._ensure(lh)
            if first:
                self._put(x, self.y + size, marker, size, HEAD_COLOR)
                first = False
            self._put(tx, self.y + size, ln, size)
            self.y += lh
        self.y += 2

    def quote(self, text: str) -> None:
        size, lh = 9.3, 9.3 * 1.5
        lines = _wrap(_strip_inline(text), size, CONTENT_W - 26)
        self._ensure(len(lines) * lh + 6)
        top = self.y
        self.page.draw_line((MARGIN + 4, top + 2), (MARGIN + 4, top + len(lines) * lh),
                            color=(0.35, 0.55, 0.90), width=2.2)
        for ln in lines:
            self._put(MARGIN + 16, self.y + size, ln, size, (0.25, 0.30, 0.40))
            self.y += lh
        self.y += 6

    def hrule(self) -> None:
        self.y += 2
        self._hline(width=0.5, pad_top=3.0)
        self.y += 2

    def table(self, header: list[str], rows: list[list[str]]) -> None:
        n = len(header)
        size = 8.0 if n <= 6 else 7.0  # 列多时缩字号，最小 7pt
        line_h = size * 1.32
        pad = 4.0
        cap = 7  # 单元格最多行数，超出截断加省略号
        # 列宽：按内容自适应（长单元格封顶，避免单列吃满）
        weights = []
        for i in range(n):
            vals = [_strip_inline(header[i])] + [_strip_inline(r[i]) for r in rows if i < len(r)]
            wmax = max((_text_w(v, size) for v in vals), default=10.0)
            weights.append(min(wmax, size * 18) + 2 * pad + 4)
        col_ws = [CONTENT_W * w / sum(weights) for w in weights]
        col_ws = [w * (CONTENT_W / sum(col_ws)) for w in [max(w, 34.0) for w in col_ws]]

        def cell_lines(text: str, col_w: float) -> list[str]:
            ls = _wrap(text, size, col_w - 2 * pad)
            if len(ls) > cap:
                ls = ls[:cap]
                last = ls[-1]
                while last and _text_w(last + "…", size) > col_w - 2 * pad:
                    last = last[:-1]
                ls[-1] = last + "…"
            return ls

        def draw_row(cells: list[str], is_header: bool = False) -> None:
            cells = (list(cells) + [""] * n)[:n]
            grid = [cell_lines(c, col_ws[i]) for i, c in enumerate(cells)]
            row_h = max(len(ls) for ls in grid) * line_h + 5
            paged = self._ensure(row_h + 3)
            if paged and not is_header:
                draw_row(header, is_header=True)  # 跨页重复表头
            top = self.y
            if is_header:
                self.page.draw_rect(fitz.Rect(MARGIN, top, PAGE_W - MARGIN, top + row_h),
                                    color=None, fill=HEADER_BG)
            x = MARGIN
            color = HEAD_COLOR if is_header else INK
            for i, ls in enumerate(grid):
                ty = top + pad + size
                for ln in ls:
                    self._put(x + pad, ty, ln, size, color)
                    ty += line_h
                x += col_ws[i]
            self.y = top + row_h
            shape = self.page.new_shape()
            shape.draw_line((MARGIN, top), (PAGE_W - MARGIN, top))
            shape.draw_line((MARGIN, self.y), (PAGE_W - MARGIN, self.y))
            x = MARGIN
            for w in col_ws[:-1]:
                x += w
                shape.draw_line((x, top), (x, self.y))
            shape.finish(color=GRID_COLOR, width=0.5)
            shape.commit()

        draw_row(header, is_header=True)
        for r in rows:
            draw_row(r)
        self.y += 10

    # -- 分隔页 ---------------------------------------------------------------
    def separator_page(self, idx: int, total: int, src: Path,
                       meta: dict[str, str], doc_title: str | None) -> None:
        # 首篇：分隔页即 PDF 第一页；后续篇：上一篇结束后另起分隔页
        self.page = self._new_page()
        self.y = MARGIN
        y = PAGE_H * 0.30
        self._put(MARGIN, y, f"第 {idx} 篇 · 共 {total} 篇", 10, (0.45, 0.45, 0.45))
        y += 52
        title = doc_title or src.stem
        for ln in _wrap(title, 20, CONTENT_W):
            self._put(MARGIN, y, ln, 20, HEAD_COLOR)
            y += 29
        y += 10
        shape = self.page.new_shape()
        shape.draw_line((MARGIN, y), (PAGE_W - MARGIN, y))
        shape.finish(color=GRID_COLOR, width=1.0)
        shape.commit()
        y += 34
        rows = [(k, meta.get(k, "—")) for k in META_KEYS]
        rows.append(("来源文档", f"data/corpus/{src.relative_to(CORPUS).as_posix()}"))
        for k, v in rows:
            self._put(MARGIN, y, f"{k}：", 10, (0.35, 0.35, 0.35))
            self._put(MARGIN + 88, y, v, 10, INK)
            y += 22
        # 正文另起新页，避免与分隔页内容重叠
        self.page = self._new_page()
        self.y = MARGIN

    def render_blocks(self, blocks) -> None:
        for block in blocks:
            tag = block[0]
            if tag == "h":
                self.heading(block[2], block[1])
            elif tag == "para":
                self.para(_strip_inline(block[1]))
            elif tag == "table":
                self.table(block[1], block[2])
            elif tag == "hr":
                self.hrule()
            elif tag == "quote":
                self.quote(block[1])
            elif tag == "li":
                self.list_item(block[1], block[2], block[3])

    def finalize(self, out: Path) -> int:
        total = self.doc.page_count
        for i, page in enumerate(self.doc, 1):
            page.insert_text((MARGIN, PAGE_H - 22), _sanitize(FOOTER_TEXT),
                             fontsize=7, fontname=FONTNAME, color=(0.5, 0.5, 0.5))
            label = f"{i} / {total}"
            page.insert_text((PAGE_W - MARGIN - FONT.text_length(label, fontsize=7),
                              PAGE_H - 22), label, fontsize=7, fontname=FONTNAME,
                             color=(0.5, 0.5, 0.5))
        self.doc.set_metadata({"title": self.title, "author": "膜法工坊 MofaLab（虚构演示品牌）",
                               "creator": "make_pdfs.py"})
        out.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(out), deflate=True, garbage=3)
        n = self.doc.page_count
        self.doc.close()
        return n


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def render_markdown_to_pdf(md_paths: list[Path], title: str, out: Path) -> int:
    """把多篇 Markdown 渲染合并为一份 PDF（篇间插入分隔页），返回页数。"""
    doc = _Doc(title)
    total = len(md_paths)
    for idx, path in enumerate(md_paths, 1):
        lines = path.read_text(encoding="utf-8").splitlines()
        meta, h1 = _parse_head(lines)
        doc.separator_page(idx, total, path, meta, h1)
        doc.render_blocks(_iter_blocks(lines))
    return doc.finalize(out)


def _dir_md(sub: str) -> list[Path]:
    return sorted((CORPUS / sub).glob("*.md"))


# PDF 名称 →（源 md 列表，验证关键字符串）
PDF_PLAN = [
    ("商品参数手册.pdf", _dir_md("product"), ["0.33mm", "透光率", "XP600", "N52"]),
    ("机型适配总表.pdf", _dir_md("compatibility"), ["Mate X6", "膜切云", "折叠屏"]),
    ("货品价格与供应链手册.pdf", _dir_md("sku"), ["经销商", "SKU", "发货时效"]),
    ("贴膜SOP与客服话术.pdf",
     [CORPUS / "manuals" / "钢化膜贴膜SOP_标准作业指导书.md",
      CORPUS / "manuals" / "私域客服话术SOP.md"],
     ["除尘贴", "灰度", "话术"]),
    ("膜切机用户手册.pdf",
     [CORPUS / "manuals" / "膜切机用户手册_MC300_MC500.md"],
     ["MC300", "MC500", "刀模"]),
    ("UV打印机操作维护手册.pdf",
     [CORPUS / "manuals" / "UV打印机操作维护手册_UV600_UV900.md"],
     ["UV600", "XP600", "白墨"]),
    ("售后服务手册.pdf", _dir_md("after_sales"), ["400-680-9527", "7天无理由", "15天换新"]),
    ("出厂质检标准.pdf", _dir_md("quality"), ["拉脱力", "透光率", "9H"]),
]


def build() -> list[tuple[str, int, list[str]]]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    removed = [p.unlink() for p in OUT_DIR.glob("*.pdf")]
    print(f"清理旧 PDF {len(removed)} 份")
    results = []
    for pdf_name, sources, keys in PDF_PLAN:
        sources = [Path(s) for s in sources]
        missing = [s.name for s in sources if not s.exists()]
        if missing:
            raise FileNotFoundError(f"{pdf_name} 缺少语料: {missing}")
        n_pages = render_markdown_to_pdf(sources, Path(pdf_name).stem, OUT_DIR / pdf_name)
        print(f"生成 {pdf_name}（合并 {len(sources)} 篇语料，{n_pages} 页）")
        results.append((pdf_name, len(sources), keys))
    return results


def verify(results: list[tuple[str, int, list[str]]]) -> bool:
    """重新打开每份 PDF：页数>0、文本可提取、关键字符串命中。"""
    ok = True
    print("-" * 76)
    for pdf_name, _, keys in results:
        with fitz.open(str(OUT_DIR / pdf_name)) as doc:
            n_pages = doc.page_count
            all_text = "".join(page.get_text() for page in doc)
        compact = re.sub(r"\s+", "", all_text)
        problems = []
        if n_pages <= 0:
            problems.append("页数为0")
        if len(compact) < 50:
            problems.append("文本不可提取")
        missing = [k for k in keys if re.sub(r"\s+", "", k) not in compact]
        status = "PASS" if not problems and not missing else "FAIL"
        if problems or missing:
            ok = False
        print(f"[{status}] {pdf_name}: {n_pages} 页, 提取 {len(all_text)} 字符, "
              f"关键字符串 {len(keys) - len(missing)}/{len(keys)} 命中"
              + (f", 异常: {problems + missing}" if problems or missing else ""))
    return ok


def main() -> int:
    print(f"字体：{FONTNAME}")
    results = build()
    return 0 if verify(results) else 1


if __name__ == "__main__":
    sys.exit(main())
