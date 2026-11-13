"""兩條主流程：SOP 審核（自評／正式審核）與 SOP 草稿生成。"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "jht-sop" / "scripts"))

from sopkit import render, verify  # noqa: E402
from sopkit.extract import as_text, extract, to_pdf  # noqa: E402
from sopkit.schemas import CHAPTER_ITEMS, ReviewResult, SopDraft, VerifyResult  # noqa: E402

from . import llm, prompts  # noqa: E402

Progress = Callable[[str], None]
VERIFY_PASS = os.environ.get("REVIEW_VERIFY_PASS", "1") == "1"


def run_review(path: Path, out_dir: Path, *, mode: str = "audit", sop_type: str = "自動判定",
               notes: str = "", progress: Progress = lambda m: None) -> dict:
    progress("擷取文件內容")
    doc = extract(path)
    if not doc["lines"]:
        raise ValueError("文件沒有可擷取的文字。")
    doc["review_date"] = date.today().isoformat()
    pdf = path if path.suffix.lower() == ".pdf" else to_pdf(path, out_dir)
    block = llm.pdf_block(pdf) if pdf else None
    doc_text = as_text(doc)

    def content(text: str):
        return ([block] if block else []) + [{"type": "text", "text": text}]

    progress("Claude 審核中（依文件長度約需 1–5 分鐘）")
    parsed, usage1 = llm.call_structured(
        prompts.review_system(mode),
        content(prompts.review_user(doc_text, review_date=doc["review_date"], sop_type=sop_type, notes=notes,
                                    warnings=doc["warnings"], has_pdf=bool(block))),
        ReviewResult, effort=llm.EFFORT_REVIEW)
    result = parsed.model_dump()
    usages = [usage1]

    # 章節檢查補齊為固定十項，避免模型漏列
    by_item = {c["item"]: c for c in result["chapters"]}
    result["chapters"] = [by_item.get(i, {"item": i, "status": "不完整", "note": "模型未回報此項目，請人工確認。"}) for i in CHAPTER_ITEMS]

    progress("逐字比對引文")
    verify.verify_quotes(doc, result["findings"])

    if mode == "audit" and VERIFY_PASS and result["findings"]:
        progress("覆核每筆缺失")
        brief = [{"id": f["id"], "category": f["category"], "severity": f["severity"], "must_fix": f["must_fix"],
                  "clause": f["clause"], "title": f["title"], "evidence": [
                      {"line_id": e["line_id"], "quote": e["quote"],
                       "程式比對": "通過" if e["verified"] else "引文未通過程式比對"} for e in f["evidence"]],
                  "problem": f["problem"], "impact": f["impact"], "recommendation": f["recommendation"],
                  "basis": f["basis"]} for f in result["findings"]]
        checked, usage2 = llm.call_structured(
            prompts.verify_system(),
            content(prompts.verify_user(doc_text, json.dumps(brief, ensure_ascii=False, indent=1),
                                        result["meta"]["sop_type"], bool(block))),
            VerifyResult, effort=llm.EFFORT_VERIFY)
        usages.append(usage2)
        verdicts = {v.id: v for v in checked.verdicts}
        for f in result["findings"]:
            v = verdicts.get(f["id"])
            if v is None:
                continue
            f["status"] = v.verdict
            if v.verdict == "調整":
                f["severity"], f["must_fix"] = v.severity, v.must_fix
            if v.verdict != "成立" and v.reason:
                f["verify_note"] = v.reason

    result["findings"] = verify.sort_findings(result["findings"])
    result["conclusion"] = verify.conclude(result)

    progress("產生 Word 報告")
    stem = path.stem[:60]
    label = "審核報告" if mode == "audit" else "自評結果"
    files = [render.render_review(result, doc, out_dir / f"{stem}_{label}.docx", mode=mode).name]
    if mode == "audit":
        files.append(render.render_tracking_xlsx(result, out_dir / f"{stem}_缺失追蹤表.xlsx").name)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"kind": "review", "mode": mode, "result": result, "files": files,
            "warnings": doc["warnings"], "usage": usages}


def run_draft(ref_paths: list[Path], out_dir: Path, *, title: str, sop_type: str, department: str = "",
              instructions: str = "", ref_text: str = "", meta: dict | None = None,
              progress: Progress = lambda m: None) -> dict:
    meta = meta or {}
    progress("讀取參考資料")
    references, warnings = [], []
    for p in ref_paths:
        d = extract(p)
        references.append((p.name, "\n".join(l["text"] for l in d["lines"])))
        warnings += [f"{p.name}：{w}" for w in d["warnings"]]
    if ref_text.strip():
        references.append(("貼上的文字", ref_text.strip()))
    total = sum(len(t) for _, t in references)
    if total > 600_000:
        raise ValueError("參考資料過長（超過約 60 萬字），請刪減為與本 SOP 直接相關的內容後再試。")
    language_note = ("語言：本 SOP 為 Critical SOP，每條須同時撰寫英文與繁體中文。" if "Critical" in sop_type
                     else "語言：本 SOP 為 Local SOP，以繁體中文撰寫，英文欄位留空。")

    progress("Claude 撰寫草稿中（約需 1–4 分鐘）")
    parsed, usage = llm.call_structured(
        prompts.draft_system(),
        prompts.draft_user(title=title, sop_type=sop_type, department=department, language_note=language_note,
                           instructions=instructions, references=references),
        SopDraft, effort=llm.EFFORT_DRAFT)
    draft = parsed.model_dump()
    draft["sop_type"] = sop_type if sop_type != "無法判定" else draft["sop_type"]

    progress("套用集團標準範本產生 Word 檔")
    safe = "".join(c for c in (draft["title_zh"] or title) if c not in '\\/:*?"<>|')[:50] or "SOP"
    out = render.render_sop(draft, meta, out_dir / f"{safe}_草稿.docx")
    (out_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    placeholders = json.dumps(draft, ensure_ascii=False).count("【待確認")
    return {"kind": "draft", "draft": draft, "files": [out.name], "warnings": warnings,
            "placeholders": placeholders, "usage": [usage]}
