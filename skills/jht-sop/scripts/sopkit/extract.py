"""把 DOCX / PDF 轉成帶行號的結構化文字，供模型引用與程式驗證。

設計重點（對應舊版的三個失準來源）：
- 表格以「列」為單位輸出（欄位用 | 分隔），保留簽核權限表、程序表的列欄關係。
- PDF 每頁重複的表頭只保留一次，標為 header。
- Word 自動編號還原成實際條號（1. / 2.1 / 2.1.1）。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

CLAUSE_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})[.、)]?(?=\s|[^\d.])")


def _clean(text: str) -> str:
    return re.sub(r"[ \t　]+", " ", text.replace("\n", " ")).strip()


class _Doc:
    def __init__(self, path: Path):
        self.path = path
        self.lines: list[dict] = []
        self.warnings: list[str] = []
        self._clause = ""

    def add(self, text: str, kind: str, page: int | None = None, clause: str | None = None):
        text = _clean(text)
        if not text:
            return
        if kind != "header":
            m = CLAUSE_RE.match(clause or text)
            if clause:
                self._clause = clause.rstrip(".")
            elif m and kind == "para":
                self._clause = m.group(1)
        self.lines.append({
            "id": f"L{len(self.lines) + 1:04d}",
            "kind": kind,
            "page": page,
            "clause": "" if kind == "header" else self._clause,
            "text": text,
        })

    def result(self) -> dict:
        return {
            "filename": self.path.name,
            "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "lines": self.lines,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------- DOCX

class _Numbering:
    """還原 Word 自動編號。只處理十進位格式，其餘（項目符號等）不加前綴。"""

    def __init__(self, doc):
        from docx.oxml.ns import qn
        self.qn = qn
        self.levels: dict[tuple[str, int], tuple[str, str, int]] = {}
        self.num_to_abs: dict[str, str] = {}
        self.counters: dict[str, dict[int, int]] = {}
        try:
            root = doc.part.numbering_part.element
        except Exception:
            return
        abstract = {}
        for a in root.findall(qn("w:abstractNum")):
            lv = {}
            for l in a.findall(qn("w:lvl")):
                fmt = l.find(qn("w:numFmt"))
                txt = l.find(qn("w:lvlText"))
                start = l.find(qn("w:start"))
                lv[int(l.get(qn("w:ilvl")))] = (
                    fmt.get(qn("w:val")) if fmt is not None else "decimal",
                    txt.get(qn("w:val")) if txt is not None else "",
                    int(start.get(qn("w:val"))) if start is not None else 1,
                )
            abstract[a.get(qn("w:abstractNumId"))] = lv
        for n in root.findall(qn("w:num")):
            ref = n.find(qn("w:abstractNumId"))
            if ref is not None:
                aid = ref.get(qn("w:val"))
                self.num_to_abs[n.get(qn("w:numId"))] = aid
                for ilvl, v in abstract.get(aid, {}).items():
                    self.levels[(aid, ilvl)] = v

    def label(self, p) -> str:
        qn = self.qn
        numpr = p._p.pPr.numPr if p._p.pPr is not None else None
        if numpr is None or numpr.numId is None:
            return ""
        num_id = str(numpr.numId.val)
        ilvl = int(numpr.ilvl.val) if numpr.ilvl is not None else 0
        aid = self.num_to_abs.get(num_id)
        if aid is None or num_id == "0":
            return ""
        fmt, text, start = self.levels.get((aid, ilvl), ("decimal", "", 1))
        c = self.counters.setdefault(aid, {})
        c[ilvl] = c.get(ilvl, start - 1) + 1
        for deeper in [k for k in c if k > ilvl]:
            del c[deeper]
        if fmt != "decimal" or not text:
            return ""
        def sub(m):
            lvl = int(m.group(1)) - 1
            return str(c.get(lvl, self.levels.get((aid, lvl), ("", "", 1))[2]))
        return re.sub(r"%(\d)", sub, text)


def _docx_table_rows(table):
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            if id(cell._tc) in seen:  # 合併儲存格只取一次
                continue
            seen.add(id(cell._tc))
            cells.append(_clean(cell.text))
        if any(cells):
            yield " | ".join(cells)


def _extract_docx(path: Path) -> dict:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    if path.suffix.lower() == ".docm":
        raise ValueError("不接受含巨集的 DOCM 檔。")
    doc = Document(str(path))
    out = _Doc(path)
    seen_header = set()
    for section in doc.sections:
        for part in (section.header, section.first_page_header):
            if part is None or part.is_linked_to_previous:
                continue
            for t in part.tables:
                for row in _docx_table_rows(t):
                    if row not in seen_header:
                        seen_header.add(row)
                        out.add(row, "header")
            for p in part.paragraphs:
                if p.text.strip() and p.text not in seen_header:
                    seen_header.add(p.text)
                    out.add(p.text, "header")
    numbering = _Numbering(doc)
    for node in doc.element.body:
        if node.tag == qn("w:p"):
            p = Paragraph(node, doc)
            label = numbering.label(p)
            if p.text.strip():
                out.add(f"{label} {p.text}" if label else p.text, "para", clause=label or None)
        elif node.tag == qn("w:tbl"):
            for row in _docx_table_rows(Table(node, doc)):
                out.add(row, "table_row")
    body = doc.element.body
    if body.findall(".//" + qn("w:ins")) or body.findall(".//" + qn("w:del")):
        out.warnings.append("文件含追蹤修訂，擷取結果可能混有未接受的修訂內容，請先接受或拒絕修訂後再送審。")
    if body.findall(".//" + qn("w:drawing")) or body.findall(".//" + qn("w:pict")):
        out.warnings.append("文件含圖片、流程圖或文字方塊，其中的文字無法擷取，相關內容須人工覆核。")
    return out.result()


# ---------------------------------------------------------------- PDF

def _header_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))


def _extract_pdf(path: Path) -> dict:
    import pymupdf

    out = _Doc(path)
    pages = []
    with pymupdf.open(str(path)) as pdf:
        if pdf.needs_pass:
            raise ValueError("PDF 已加密，請先解除密碼保護。")
        for page in pdf:
            items = []  # (y, x, kind, text)
            boxes = []
            try:
                for t in page.find_tables().tables:
                    rows = []
                    for r in t.rows:
                        cells = [_clean(page.get_text("text", clip=c)) for c in r.cells if c]
                        cells = [c for c in cells if c]
                        if cells:
                            rows.append(" | ".join(cells))
                    if len(rows) >= 2:
                        box = pymupdf.Rect(t.bbox)
                        boxes.append(box)
                        step = box.height / max(len(rows), 1)
                        for i, row in enumerate(rows):
                            items.append((box.y0 + i * step, box.x0, "table_row", row))
            except Exception:  # 表格偵測失敗時退回純文字
                boxes = []
            lines = []  # 逐視覺行讀取，再依條號與句尾合併成段
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                for ln in b["lines"]:
                    text = "".join(sp["text"] for sp in ln["spans"])
                    rect = pymupdf.Rect(ln["bbox"])
                    if not text.strip() or rect.get_area() <= 0:
                        continue
                    if any((box & rect).get_area() > 0.6 * rect.get_area() for box in boxes):
                        continue
                    lines.append((rect.y0, rect.x0, text))
            lines.sort(key=lambda l: (round(l[0] / 3), l[1]))
            merged = []  # [y, x, text]
            for y, x, text in lines:
                same_row = merged and abs(merged[-1][3] - y) < 3
                starts_new = bool(CLAUSE_RE.match(text)) or text.lstrip().startswith(("-", "•", "‧"))
                ended = merged and merged[-1][2].rstrip().endswith(("。", ".", "：", ":", "；", ";"))
                if merged and (same_row or not (starts_new or ended)):
                    merged[-1][2] += " " + text
                    merged[-1][3] = y
                else:
                    merged.append([y, x, text, y])
            for y, x, text, _ in merged:
                items.append((y, x, "para", text))
            items.sort(key=lambda i: (round(i[0] / 3), i[1]))
            pages.append(items)
            if not items:
                out.warnings.append(f"第 {page.number + 1} 頁沒有可擷取的文字（可能是掃描影像），該頁內容須人工覆核。")
    # 每頁重複出現的內容視為表頭；表頭帶（頁面上緣到最後一個重複項目）只在第一頁輸出一次
    counts = Counter()
    for items in pages:
        for key in {_header_key(i[3]) for i in items}:
            counts[key] += 1
    repeated = {k for k, v in counts.items() if len(pages) >= 2 and v >= max(2, round(len(pages) * 0.6))}
    band = max((i[0] for items in pages for i in items if _header_key(i[3]) in repeated and i[0] < 250), default=-1)
    for n, items in enumerate(pages, 1):
        for y, _, kind, text in items:
            if y <= band + 2 or _header_key(text) in repeated or (n > 1 and "Approved by" in text and "Prepared by" in text):
                if n == 1:
                    out.add(text, "header", page=n)
                continue
            out.add(text, kind, page=n)
    if not out.lines:
        raise ValueError("PDF 沒有可擷取的文字（可能是掃描檔），請提供文字版 PDF 或 Word 檔。")
    return out.result()


# ---------------------------------------------------------------- 入口

def to_pdf(path: Path, out_dir: Path) -> Path | None:
    """用 LibreOffice 把 Word 轉成 PDF（有安裝才做），讓模型看得到版面與頁碼。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    try:
        with tempfile.TemporaryDirectory() as profile:
            subprocess.run(
                [soffice, f"-env:UserInstallation=file://{profile}", "--headless",
                 "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
                check=True, capture_output=True, timeout=120,
            )
        pdf = out_dir / (path.stem + ".pdf")
        return pdf if pdf.exists() else None
    except Exception:
        return None


def extract(path: str | Path) -> dict:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in (".txt", ".md"):
        out = _Doc(path)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            out.add(line, "para")
        return out.result()
    if suffix == ".doc":
        with tempfile.TemporaryDirectory() as tmp:
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", tmp, str(path)],
                               capture_output=True, timeout=120)
                converted = Path(tmp) / (path.stem + ".docx")
                if converted.exists():
                    result = _extract_docx(converted)
                    result["filename"] = path.name
                    return result
        raise ValueError("舊版 .doc 無法直接讀取，請另存為 .docx 或 PDF 後再上傳。")
    raise ValueError(f"不支援的檔案格式 {suffix}；請上傳 DOCX、PDF 或 TXT。")


def as_text(doc: dict) -> str:
    """給模型看的行號版全文。"""
    rows = []
    for l in doc["lines"]:
        tag = {"header": "表頭", "table_row": "表格"}.get(l["kind"], "")
        page = f" p{l['page']}" if l["page"] else ""
        rows.append(f"[{l['id']}{page}{' ' + tag if tag else ''}] {l['text']}")
    return "\n".join(rows)
