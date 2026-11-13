#!/usr/bin/env python3
"""喬山 SOP skill 的命令列工具（不呼叫模型）。

  sop.py extract  <SOP.docx|pdf> [--json out.json]     輸出帶行號的全文，供審核時引用
  sop.py review   <SOP.docx|pdf> <review.json> --out DIR  驗證引文並產生 Word 審核報告與 Excel 追蹤表
  sop.py draft    <draft.json> --out FILE.docx [--control-number ..] [--prepared-by ..] [--approved-by ..]
                                                        把草稿套入集團標準範本
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sopkit import render, verify  # noqa: E402
from sopkit.extract import as_text, extract  # noqa: E402
from sopkit.schemas import CHAPTER_ITEMS, ReviewResult, SopDraft  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("file"); e.add_argument("--json")
    r = sub.add_parser("review"); r.add_argument("file"); r.add_argument("review_json"); r.add_argument("--out", required=True)
    r.add_argument("--mode", choices=["audit", "self"], default="audit")
    d = sub.add_parser("draft"); d.add_argument("draft_json"); d.add_argument("--out", required=True)
    for k in ("control-number", "prepared-by", "approved-by", "rev", "effective-date"):
        d.add_argument(f"--{k}", default="")
    a = ap.parse_args()
    try:
        if a.cmd == "extract":
            doc = extract(a.file)
            if a.json:
                Path(a.json).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            print(as_text(doc))
            for w in doc["warnings"]:
                print(f"[注意] {w}", file=sys.stderr)
        elif a.cmd == "review":
            doc = extract(a.file)
            doc["review_date"] = date.today().isoformat()
            result = ReviewResult.model_validate_json(Path(a.review_json).read_text(encoding="utf-8")).model_dump()
            by = {c["item"]: c for c in result["chapters"]}
            result["chapters"] = [by.get(i, {"item": i, "status": "不完整", "note": "未回報此項目，請確認。"}) for i in CHAPTER_ITEMS]
            verify.verify_quotes(doc, result["findings"])
            out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
            stem = Path(a.file).stem
            print(render.render_review(result, doc, out / f"{stem}_{'審核報告' if a.mode == 'audit' else '自評結果'}.docx", mode=a.mode))
            print(render.render_tracking_xlsx(result, out / f"{stem}_缺失追蹤表.xlsx"))
            concl = verify.conclude(result)
            print(json.dumps(concl, ensure_ascii=False))
            if concl["unverified_ids"]:
                print(f"[注意] 引文未通過逐字比對：{concl['unverified_ids']}——請修正 review.json 的引文後重跑。", file=sys.stderr)
        else:
            draft = SopDraft.model_validate_json(Path(a.draft_json).read_text(encoding="utf-8")).model_dump()
            meta = {"control_number": a.control_number, "prepared_by": a.prepared_by, "approved_by": a.approved_by,
                    "rev": a.rev, "effective_date": a.effective_date}
            print(render.render_sop(draft, meta, a.out))
    except (ValueError, OSError) as exc:
        sys.exit(f"錯誤：{exc}")


if __name__ == "__main__":
    main()
