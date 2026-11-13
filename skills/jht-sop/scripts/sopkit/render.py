"""Word / Excel 輸出：SOP 草稿（套用集團標準範本）與審核報告。"""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

from .verify import conclude, sort_findings

TEMPLATE = Path(__file__).resolve().parents[2] / "assets" / "sop-template.docx"
FONT = "微軟正黑體"
SEV_COLOR = {"重大": "C00000", "高": "E36C0A", "中": "BF8F00", "低": "7F7F7F"}
CHAPTERS = [("Purpose", "purpose"), ("Scope", "scope"), ("SOP Maintenance", "maintenance"),
            ("Definition", "definition"), ("Responsibility", "responsibility"), ("Process", "process"),
            ("Control", "control"), ("Documents", "documents")]


# ============================================================ SOP 草稿

def _set_cell_value(cell, value: str):
    """表頭儲存格為「標籤段落 + 值段落」；只改第二段的值。"""
    paras = cell.paragraphs
    target = paras[1] if len(paras) > 1 else cell.add_paragraph()
    for r in target.runs[1:]:
        r._r.getparent().remove(r._r)
    if target.runs:
        target.runs[0].text = value
    else:
        target.add_run(value)


def _fill_header(doc, meta: dict, title_en: str, title_zh: str):
    table = doc.sections[0].header.tables[0]
    _set_cell_value(table.cell(0, 0), meta.get("rev") or "1")
    _set_cell_value(table.cell(0, 2), meta.get("control_number") or "【待填】")
    _set_cell_value(table.cell(1, 0), meta.get("effective_date") or "First submission")
    name = table.cell(1, 1)
    p = name.paragraphs[0]
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    p.runs[0].text = title_en
    extra = copy.deepcopy(p._p)
    p._p.addnext(extra)
    Paragraph(extra, name).runs[0].text = title_zh
    _set_cell_value(table.cell(2, 0), meta.get("revised_date") or "")
    if meta.get("approved_by"):
        _set_cell_value(table.cell(2, 1), meta["approved_by"])
    _set_cell_value(table.cell(2, 2), meta.get("prepared_by") or "")


def _body_para(sample, text: str, indent: int = 294, bold: bool = False):
    el = copy.deepcopy(sample)
    for r in el.findall(qn("w:r"))[1:]:
        el.remove(r)
    ind = el.find(qn("w:pPr")).find(qn("w:ind"))
    if ind is not None:
        ind.set(qn("w:start"), str(indent))
    run = el.find(qn("w:r"))
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    if bold:
        run.find(qn("w:rPr")).append(OxmlElement("w:b"))
    return el


def _cell_borders(cell):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for side in ("top", "start", "bottom", "end"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "single"); b.set(qn("w:sz"), "4"); b.set(qn("w:space"), "0"); b.set(qn("w:color"), "000000")
        borders.append(b)
    tcPr.append(borders)


def _style_cell(cell, size=10, bold=False, fill=None):
    for p in cell.paragraphs:
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.name = "Arial"
            r._r.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    if fill:
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        cell._tc.get_or_add_tcPr().append(shd)


def _set_widths(table, widths):
    """同時設定欄寬與儲存格寬，Word 與 LibreOffice 才都會採用。"""
    table.autofit = False
    for col, w in zip(table.columns, widths):
        col.width = w
    for row in table.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = w


def _process_table(doc, flow: dict, bilingual: bool):
    table = doc.add_table(rows=1, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [Cm(3.2), Cm(8.2), Cm(2.8), Cm(2.8)]
    for cell, text in zip(table.rows[0].cells, ["Process\n流程", "Instruction\n說明", "Control Doc.\n表格", "Responsibility\n權責單位"]):
        cell.text = text
        _style_cell(cell, bold=True, fill="D9D9D9")
    for n, s in enumerate(flow["steps"], 1):
        row = table.add_row().cells
        row[0].text = f"{n}. {s['process']}"
        text = s["instruction_zh"]
        if bilingual and s.get("instruction_en"):
            text = f"{s['instruction_en']}\n{s['instruction_zh']}"
        row[1].text = text
        row[2].text = s.get("control_doc", "")
        row[3].text = s.get("responsibility", "")
        for c in row:
            _style_cell(c)
    _set_widths(table, widths)
    for row in table.rows:
        for cell in row.cells:
            _cell_borders(cell)
    return table._tbl


def render_sop(draft: dict, meta: dict, out_path: str | Path) -> Path:
    """把草稿填入集團標準範本。meta: control_number, rev, effective_date, approved_by, prepared_by。"""
    doc = Document(str(TEMPLATE))
    bilingual = "Critical" in draft.get("sop_type", "") or any(c.get("en") for c in draft.get("purpose", []))
    _fill_header(doc, meta, draft["title_en"], draft["title_zh"])
    body = doc.element.body
    paras = [el for el in body if el.tag == qn("w:p")]
    headings, sample = {}, None
    for el in paras:
        text = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
        for en, key in CHAPTERS:
            if text.startswith(en):
                headings[key] = el
        if not text and sample is None and el.find(qn("w:pPr")) is not None and el.find(qn("w:pPr")).find(qn("w:ind")) is not None:
            sample = el
    sample = copy.deepcopy(sample)
    last_heading_idx = max(list(body).index(h) for h in headings.values())
    table_idx = next(i for i, el in enumerate(body) if el.tag == qn("w:tbl"))
    for i, el in enumerate(list(body)):
        if el.tag == qn("w:p") and el not in headings.values() and i < table_idx:
            body.remove(el)

    def clause_elements(clauses):
        out = []
        for c in clauses:
            num = (c.get("number") or "").strip()
            prefix = f"{num} " if num else ""
            depth = num.count(".")
            indent = 294 + 280 * max(depth - 1, 0)
            en, zh = (c.get("en") or "").strip(), (c.get("zh") or "").strip()
            if bilingual and en:
                out.append(_body_para(sample, prefix + en, indent))
                if zh:
                    out.append(_body_para(sample, zh, indent + (len(prefix) * 110 if prefix else 0)))
            else:
                out.append(_body_para(sample, prefix + (zh or en), indent))
        return out

    for _, key in CHAPTERS:
        anchor = headings[key]
        elements = []
        if key == "process":
            for flow in draft.get("process", []):
                title = f"{flow['number']} {flow['title_en']} {flow['title_zh']}".replace("  ", " ").strip()
                elements.append(_body_para(sample, title, bold=True))
                elements.append(_process_table(doc, flow, bilingual))
                elements.append(_body_para(sample, ""))
        else:
            elements = clause_elements(draft.get(key, []))
        elements.append(_body_para(sample, ""))
        for el in elements:
            anchor.addnext(el)
            anchor = el
    out_path = Path(out_path)
    doc.save(str(out_path))
    return out_path


# ============================================================ 審核報告

def _new_doc(title: str, landscape: bool = False):
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Arial"
    st.font.size = Pt(10.5)
    st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    for name in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        s = doc.styles[name]
        s.font.name = "Arial"
        s.font.color.rgb = RGBColor(0x1F, 0x2A, 0x44)
        s.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(2)
    doc.add_heading(title, 0)
    return doc


def _kv_table(doc, rows, widths=(Cm(3.6), Cm(13.4)), header_fill="F2F2F2"):
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.autofit = False
    for k, v in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = k, str(v or "")
        _style_cell(cells[0], size=10, bold=True, fill=header_fill)
        _style_cell(cells[1], size=10)
    _set_widths(table, widths)
    return table


def _grid(doc, header, rows, widths):
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.autofit = False
    for c, h in zip(table.rows[0].cells, header):
        c.text = h
        _style_cell(c, bold=True, fill="D9E2F3")
    for r in rows:
        cells = table.add_row().cells
        for c, v in zip(cells, r):
            c.text = str(v or "")
            _style_cell(c)
    _set_widths(table, widths)
    return table


def _finding_block(doc, f: dict, reply: bool):
    h = doc.add_heading(level=3)
    r = h.add_run(f"{f['id']}　［{f['severity']}］{f.get('title') or f['category']}")
    r.font.color.rgb = RGBColor.from_string(SEV_COLOR.get(f["severity"], "000000"))
    quotes = []
    for e in f.get("evidence", []):
        loc = "、".join(x for x in [f"第 {e['page']} 頁" if e.get("page") else "", f"條文 {e['line_clause']}" if e.get("line_clause") else "", e.get("line_id", "")] if x)
        mark = "" if e.get("verified", True) else "　⚠ 此引文未能在原文逐字比對到，請人工核對"
        quotes.append(f"「{e['quote']}」（{loc}）{mark}")
    rows = [
        ("面向／條文", f"{f['category']}　｜　{f.get('clause') or '—'}"),
        ("原文", "\n".join(quotes) or "—"),
        ("問題說明", f["problem"]),
        ("影響", f["impact"]),
        ("修正建議", f["recommendation"]),
    ]
    if f.get("suggested_text_zh") or f.get("suggested_text_en"):
        rows.append(("建議條文", "\n".join(x for x in [f.get("suggested_text_en"), f.get("suggested_text_zh")] if x)))
    rows.append(("判斷性質", f"{f['basis']}　｜　送簽前必改：{'是' if f.get('must_fix') else '否'}"))
    if f.get("verify_note"):
        rows.append(("覆核備註", f["verify_note"]))
    if reply:
        rows.append(("原單位回覆", "□ 已修正　□ 部分修正　□ 維持原文（請說明）\n\n"))
    _kv_table(doc, rows)
    doc.add_paragraph()


def render_review(result: dict, doc_info: dict, out_path: str | Path, mode: str = "audit") -> Path:
    """result: ReviewResult（findings 已經 verify_quotes）；doc_info: 擷取結果（filename/sha256/warnings）。"""
    concl = conclude(result)
    meta = result["meta"]
    title = "SOP 審核報告" if mode == "audit" else "SOP 自評結果"
    doc = _new_doc(title)
    doc.add_paragraph(
        "本報告為送簽前的審核建議，供 SOP 撰寫單位修正與核准主管參考，不代表文件已獲核准。" if mode == "audit"
        else "本結果供撰寫人送審前自我檢查，正式審核意見以總部經管審核報告為準。")

    doc.add_heading("一、審核結論", 1)
    c = concl["counts"]
    _kv_table(doc, [
        ("送簽建議", concl["verdict"]),
        ("缺失統計", f"重大 {c['重大']}　高 {c['高']}　中 {c['中']}　低 {c['低']}　｜　送簽前必改 {len(concl['must_fix_ids'])} 項"),
        ("參考分數", f"{concl['score']} / 100（依缺失嚴重度扣分，僅供版本間比較）"),
        ("總評", result.get("summary", "")),
    ])
    if result.get("priorities"):
        doc.add_heading("優先處理事項", 2)
        for p in result["priorities"]:
            doc.add_paragraph(p, style="List Number")
    if result.get("strengths"):
        doc.add_heading("文件優點", 2)
        for s in result["strengths"]:
            doc.add_paragraph(s, style="List Bullet")

    doc.add_heading("二、文件基本資料", 1)
    _kv_table(doc, [
        ("SOP 名稱", f"{meta.get('title_en', '')}\n{meta.get('title_zh', '')}".strip()),
        ("文件編號／版次", f"{meta.get('control_number') or '（未載明）'}　／　Rev. {meta.get('rev') or '（未載明）'}"),
        ("生效日", meta.get("effective_date") or "（未載明）"),
        ("核准／制定", f"{meta.get('approved_by') or '（未載明）'}　／　{meta.get('prepared_by') or '（未載明）'}"),
        ("SOP 類別", f"{meta.get('sop_type', '')}　—　{meta.get('sop_type_reason', '')}"),
        ("語言", meta.get("language", "")),
        ("審核檔案", f"{doc_info.get('filename', '')}\nSHA-256 {doc_info.get('sha256', '')[:16]}…"),
        ("審核日期", doc_info.get("review_date") or date.today().isoformat()),
    ])

    doc.add_heading("三、標準格式檢查", 1)
    _grid(doc, ["項目", "結果", "說明"],
          [(ch["item"], ch["status"], ch.get("note", "")) for ch in result.get("chapters", [])],
          [Cm(5), Cm(2), Cm(10)])

    findings = sort_findings([f for f in result["findings"] if f.get("status", "成立") != "不成立"])
    doc.add_heading("四、缺失總表", 1)
    if findings:
        _grid(doc, ["編號", "嚴重度", "面向", "條文", "摘要", "必改"],
              [(f["id"], f["severity"], f["category"], f.get("clause", ""), f.get("title", ""), "是" if f.get("must_fix") else "") for f in findings],
              [Cm(1.4), Cm(1.6), Cm(3), Cm(2), Cm(7.6), Cm(1.4)])
    else:
        doc.add_paragraph("本次審核未發現缺失。")

    doc.add_heading("五、缺失明細與修正建議", 1)
    for f in findings:
        _finding_block(doc, f, reply=(mode == "audit"))

    dropped = [f for f in result["findings"] if f.get("status") == "不成立"]
    notes = list(doc_info.get("warnings", []))
    if concl["unverified_ids"]:
        notes.append(f"下列缺失的引文未能與原文逐字比對（可能來自表格或流程圖版面），請人工核對：{'、'.join(concl['unverified_ids'])}。")
    if dropped or notes:
        doc.add_heading("六、審核範圍與限制", 1)
        for n in notes:
            doc.add_paragraph(n, style="List Bullet")
        if dropped:
            doc.add_paragraph("以下項目在初審提出、經覆核後認定不成立，未列入缺失，僅供審核人員參考：")
            for f in dropped:
                doc.add_paragraph(f"{f['id']} {f.get('title', '')}：{f.get('verify_note', '')}", style="List Bullet")
    out_path = Path(out_path)
    doc.save(str(out_path))
    return out_path


def render_tracking_xlsx(result: dict, out_path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "缺失追蹤"
    header = ["編號", "嚴重度", "面向", "條文", "摘要", "原文", "問題說明", "修正建議", "建議條文", "必改",
              "判斷性質", "負責人", "預計完成日", "狀態", "複審結果"]
    ws.append(header)
    for f in sort_findings([f for f in result["findings"] if f.get("status", "成立") != "不成立"]):
        row = [f["id"], f["severity"], f["category"], f.get("clause", ""), f.get("title", ""),
               "\n".join(e["quote"] for e in f.get("evidence", [])), f["problem"], f["recommendation"],
               "\n".join(x for x in [f.get("suggested_text_en"), f.get("suggested_text_zh")] if x),
               "是" if f.get("must_fix") else "否", f["basis"], "", "", "未結案", ""]
        # 防止試算表公式注入
        ws.append([("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v for v in row])
    widths = [7, 8, 14, 8, 24, 40, 40, 40, 40, 6, 14, 12, 12, 10, 14]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E2F3")
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    out_path = Path(out_path)
    wb.save(str(out_path))
    return out_path
