"""以程式逐字驗證模型引用的原文，並計算送簽建議。"""
from __future__ import annotations

import re
import unicodedata

PENALTY = {"重大": 25, "高": 10, "中": 4, "低": 1}
ORDER = {"重大": 0, "高": 1, "中": 2, "低": 3}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[‘’“”「」'\"]", '"', text)
    return re.sub(r"\s+", "", text)


def verify_quotes(doc: dict, findings: list[dict]) -> list[dict]:
    """為每段引文標記 verified；行號引錯但原文確實存在時自動更正行號。"""
    lines = doc["lines"]
    by_id = {l["id"]: l for l in lines}
    normed = [(l, norm(l["text"])) for l in lines]
    # 整份文件串接，用來比對跨行引文
    offsets, whole, pos = [], [], 0
    for l, n in normed:
        offsets.append((pos, l))
        whole.append(n)
        pos += len(n)
    whole = "".join(whole)

    def locate(q: str):
        i = whole.find(q)
        if i < 0:
            return None
        hit = offsets[0][1]
        for start, l in offsets:
            if start > i:
                break
            hit = l
        return hit

    for f in findings:
        for e in f.get("evidence", []):
            q = norm(e.get("quote", ""))
            line = by_id.get(e.get("line_id"))
            ok = bool(q) and line is not None and q in norm(line["text"])
            if not ok and q:
                found = locate(q)
                if found is not None:
                    line, ok = found, True
                    e["line_id"] = found["id"]
            e["verified"] = ok
            e["page"] = line["page"] if line else None
            e["line_clause"] = line["clause"] if line else ""
        ev = f.get("evidence", [])
        f["quotes_verified"] = bool(ev) and all(e["verified"] for e in ev)
    return findings


def conclude(result: dict) -> dict:
    """依缺失與章節檢查計算參考分數與送簽建議（規則固定，不由模型決定）。"""
    active = [f for f in result["findings"] if f.get("status", "成立") != "不成立"]
    counts = {s: sum(1 for f in active if f["severity"] == s) for s in PENALTY}
    missing = [c["item"] for c in result.get("chapters", []) if c["status"] == "缺漏"]
    score = max(0, 100 - sum(PENALTY[f["severity"]] for f in active) - 5 * len(missing))
    must = [f["id"] for f in active if f.get("must_fix")]
    if counts["重大"] or len(missing) >= 2:
        verdict = "退回修正（存在重大缺失或缺少必要章節）"
    elif counts["高"] or must or missing:
        verdict = "修正後再送簽"
    else:
        verdict = "可送簽（仍須由審核人員確認）"
    unverified = [f["id"] for f in active if not f.get("quotes_verified", True)]
    return {"verdict": verdict, "score": score, "counts": counts, "must_fix_ids": must,
            "missing_chapters": missing, "unverified_ids": unverified}


def sort_findings(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (ORDER.get(f["severity"], 9), f["id"]))
