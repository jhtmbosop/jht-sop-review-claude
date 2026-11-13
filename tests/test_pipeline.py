"""離線測試：以假的模型回應驗證擷取、引文比對、結論規則、Word/Excel 輸出與 API 流程。不呼叫 Claude。"""
import sys
import time
from pathlib import Path

import pytest
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills" / "jht-sop" / "scripts"))

from sopkit import render, verify  # noqa: E402
from sopkit.extract import as_text, extract  # noqa: E402
from sopkit.schemas import CHAPTER_ITEMS, ReviewResult, SopDraft, VerifyResult  # noqa: E402

PDF = ROOT / "reference-docs" / "TT-QP-CO-216_SOP_approval_procedure.pdf"


def finding(i, sev, line, quote, **kw):
    return {"id": f"F{i:02d}", "category": "跨條文一致性", "severity": sev, "clause": "4.3.2", "title": "時限要求不一致",
            "evidence": [{"line_id": line, "quote": quote}], "problem": "p", "impact": "i", "recommendation": "r",
            "suggested_text_zh": "建議中文", "suggested_text_en": "", "basis": "文件確認事實", "must_fix": sev in ("重大", "高"), **kw}


def review_payload(findings):
    return {"meta": {"title_zh": "喬山集團 SOP 審核程序", "title_en": "SOP for Johnson Group SOP approval procedure",
                     "control_number": "TT-QP-CO-216", "rev": "1", "effective_date": "2026-05-07", "approved_by": "A",
                     "prepared_by": "B", "sop_type": "Group Critical SOP", "sop_type_reason": "適用全集團", "language": "中英對照"},
            "chapters": [{"item": i, "status": "符合", "note": ""} for i in CHAPTER_ITEMS[:-1]],  # 故意漏一項
            "findings": findings, "strengths": ["架構完整"], "summary": "總評", "priorities": ["先處理 F01"]}


def draft_payload():
    c = lambda n, zh, en="": {"number": n, "en": en, "zh": zh}
    return {"title_en": "SOP for Fixed Asset Disposal", "title_zh": "固定資產報廢作業", "sop_type": "Group Critical SOP",
            "purpose": [c("", "規範固定資產報廢。", "To regulate disposal.")], "scope": [c("2.1", "適用所有子公司。", "All affiliates.")],
            "maintenance": [c("3.1", "由總部財務維護。", "Maintained by HQ Finance.")], "definition": [c("4.1", "固定資產：…", "Fixed asset: …")],
            "responsibility": [c("5.1", "申請單位：提出申請。", "Requester: submit."), c("5.1.1", "子項", "Sub item")],
            "process": [{"number": "6.1", "title_en": "Disposal", "title_zh": "報廢流程", "steps": [
                {"process": "提出申請", "instruction_en": "Submit within 【待確認：days】.", "instruction_zh": "於【待確認：幾個工作天】內提出。",
                 "control_doc": "報廢申請單", "responsibility": "申請單位"}]}],
            "control": [c("7.1", "報廢須經核准。", "Disposal shall be approved.")], "documents": [c("8.1", "報廢申請單", "Disposal form")],
            "open_questions": [{"clause": "6.1", "question": "申請期限", "reason": "參考資料未提供"}], "source_summary": "取自參考資料。"}


def test_extract_pdf_structure():
    doc = extract(PDF)
    text = as_text(doc)
    headers = [l for l in doc["lines"] if l["kind"] == "header"]
    assert sum("Control Number" in l["text"] for l in doc["lines"]) == 1, "重複表頭應只保留一次"
    assert headers and all(l["page"] == 1 for l in headers)
    row = next(l["text"] for l in doc["lines"] if l["text"].startswith("Group Critical SOP |"))
    assert row.count("|") == 8 and row.endswith("Approve 准"), "簽核權限表應保留完整列欄"
    assert any(l["clause"] == "7.2" for l in doc["lines"])
    assert "[L0001 p1 表頭]" in text


def test_extract_docx_numbering_and_tables(tmp_path):
    doc = extract(ROOT / "skills/jht-sop/assets/sop-template.docx")
    texts = [l["text"] for l in doc["lines"]]
    assert "1. Purpose 目的" in texts and "8. Documents 使用文件" in texts, "Word 自動編號應還原"
    assert any(l["kind"] == "table_row" and "條文Clause" in l["text"] for l in doc["lines"])


def test_verify_quotes_and_relocation():
    doc = extract(PDF)
    real = next(l for l in doc["lines"] if l["text"].startswith("7.2 Affiliates shall update"))
    fs = [finding(1, "高", real["id"], "Affiliates shall update their   Affiliate Critical SOPs within two months"),  # 空白差異仍通過
          finding(2, "中", "L0001", "二個月內進行Affiliate Critical SOP 更新"),  # 行號錯但原文存在 -> 自動更正
          finding(3, "低", real["id"], "此句不存在於文件")]
    verify.verify_quotes(doc, fs)
    assert fs[0]["quotes_verified"] and fs[0]["evidence"][0]["page"] == 9
    assert fs[1]["quotes_verified"] and fs[1]["evidence"][0]["line_id"] != "L0001"
    assert not fs[2]["quotes_verified"]


@pytest.mark.parametrize("sevs,missing,expect", [
    (["重大"], 0, "退回"), (["高", "低"], 0, "修正後"), (["低"], 0, "可送簽"), ([], 2, "退回"), ([], 1, "修正後")])
def test_conclusion_rules(sevs, missing, expect):
    r = {"findings": [finding(i, s, "L1", "x") for i, s in enumerate(sevs)],
         "chapters": [{"item": f"c{i}", "status": "缺漏", "note": ""} for i in range(missing)]}
    assert verify.conclude(r)["verdict"].startswith(expect)


def test_dropped_findings_do_not_count():
    r = {"findings": [finding(1, "重大", "L1", "x", status="不成立")], "chapters": []}
    c = verify.conclude(r)
    assert c["score"] == 100 and c["verdict"].startswith("可送簽")


def test_render_sop_uses_template(tmp_path):
    out = render.render_sop(draft_payload(), {"control_number": "TT-QP-FN-001", "prepared_by": "王小明"}, tmp_path / "d.docx")
    doc = Document(str(out))
    header = "\n".join(c.text for r in doc.sections[0].header.tables[0].rows for c in r.cells)
    assert "TT-QP-FN-001" in header and "SOP for Fixed Asset Disposal" in header and "固定資產報廢作業" in header and "王小明" in header
    back = extract(out)
    texts = [l["text"] for l in back["lines"]]
    order = [next(i for i, t in enumerate(texts) if t.startswith(f"{n}. ")) for n in range(1, 9)]
    assert order == sorted(order), "八章順序須維持"
    assert any("2.1 All affiliates." in t for t in texts)
    assert any(t.startswith("Process 流程 | Instruction 說明") for t in texts)
    assert any("SOP修改對照表" in t for t in texts), "修改對照表須保留"


def fake_llm(monkeypatch, verdicts=None):
    from app import llm
    doc = extract(PDF)
    real = next(l for l in doc["lines"] if l["text"].startswith("7.2 Affiliates shall update"))
    payload = review_payload([finding(1, "高", real["id"], "within two months"), finding(2, "中", real["id"], "不存在的引文")])
    calls = []

    def call(system, content, schema, *, effort):
        calls.append(schema.__name__)
        if schema is ReviewResult:
            return ReviewResult.model_validate(payload), {"model": "fake"}
        if schema is VerifyResult:
            return VerifyResult.model_validate({"verdicts": verdicts or []}), {"model": "fake"}
        return SopDraft.model_validate(draft_payload()), {"model": "fake"}

    monkeypatch.setattr(llm, "call_structured", call)
    return calls


def test_run_review_audit(tmp_path, monkeypatch):
    from app import services
    calls = fake_llm(monkeypatch, [{"id": "F01", "verdict": "成立", "reason": "", "severity": "高", "must_fix": True},
                                   {"id": "F02", "verdict": "不成立", "reason": "引文不存在", "severity": "中", "must_fix": False}])
    out = services.run_review(PDF, tmp_path, mode="audit")
    assert calls == ["ReviewResult", "VerifyResult"]
    res = out["result"]
    assert len(res["chapters"]) == 10 and res["chapters"][-1]["status"] == "不完整", "漏報的章節項目須補齊"
    assert res["conclusion"]["counts"] == {"重大": 0, "高": 1, "中": 0, "低": 0}
    assert [f.suffix for f in map(Path, out["files"])] == [".docx", ".xlsx"]
    text = "\n".join(c.text for t in Document(str(tmp_path / out["files"][0])).tables for r in t.rows for c in r.cells)
    assert "within two months" in text and "修正後再送簽" in text and "不存在的引文" not in text


def test_run_review_self_skips_verify_pass(tmp_path, monkeypatch):
    from app import services
    calls = fake_llm(monkeypatch)
    out = services.run_review(PDF, tmp_path, mode="self")
    assert calls == ["ReviewResult"] and len(out["files"]) == 1
    assert out["result"]["conclusion"]["unverified_ids"] == ["F02"]


def test_api_draft_then_selfcheck(tmp_path, monkeypatch):
    monkeypatch.setenv("SOP_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "pw")
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, "WORK", tmp_path)
    fake_llm(monkeypatch)
    c = TestClient(main.app)
    assert c.get("/").status_code == 401
    auth = ("sop", "pw")
    assert c.post("/api/draft", data={"title": "x", "sop_type": "Group Critical SOP"}, auth=auth).status_code == 400

    def wait(jid):
        for _ in range(100):
            j = c.get(f"/api/jobs/{jid}", auth=auth).json()
            if j["status"] != "running":
                return j
            time.sleep(0.1)
        raise AssertionError("timeout")

    r = c.post("/api/draft", auth=auth, data={"title": "固定資產報廢", "sop_type": "Group Critical SOP", "ref_text": "流程說明"},
               files=[("files", ("ref.txt", "現行做法：先申請後核准".encode(), "text/plain"))])
    j = wait(r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["placeholders"] == 2
    name = j["result"]["files"][0]
    assert c.get(f"/api/jobs/{j['id']}/files/{name}", auth=auth).status_code == 200
    assert c.get(f"/api/jobs/{j['id']}/files/../main.py", auth=auth).status_code == 404
    r = c.post("/api/review", auth=auth, data={"from_job": j["id"], "mode": "self"})
    assert wait(r.json()["job_id"])["status"] == "done"
    assert c.post("/api/review", auth=auth, data={"mode": "audit"}, files={"file": ("a.exe", b"x")}).status_code == 400
