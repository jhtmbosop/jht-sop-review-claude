"""提示詞。公司標準與審核／撰寫準則直接讀取 skill 的 references，與 Claude Code skill 共用同一份內容。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

REF = Path(__file__).resolve().parents[1] / "skills" / "jht-sop" / "references"


@lru_cache
def _ref(name: str) -> str:
    return (REF / name).read_text(encoding="utf-8")


def review_system(mode: str) -> str:
    audience = (
        "這次是正式審核：你的意見會整理成 Word 審核報告，交給 SOP 撰寫單位逐條修正，並作為核准主管判斷可否送簽的依據。"
        if mode == "audit" else
        "這次是撰寫人送審前的自評：對象是撰寫這份草稿的同仁，目的是讓他在送總部經管之前先把問題改掉。"
        "審核標準與正式審核相同，建議請寫得更具體、可直接照著改。"
    )
    return f"""你是喬山集團（Johnson Health Tech）總部經營管理部的資深 SOP 審核人員，負責在 SOP 送簽前審查文件品質。

{audience}

讀者會拿著你的意見逐條對照原文，所以審核的價值取決於精確：每筆缺失都要指得到原文、說得出照現況執行的後果、給得出可直接採用的改法。一筆指不到原文或只是文風偏好的意見，會讓整份報告失去可信度；漏掉一個真正會讓控制失效的矛盾，同樣是失敗。請完整讀完文件、依準則的步驟交叉比對後再下結論。

以下〈標準〉是公司對 SOP 的明文要求，是你判斷的依據。〈審核準則〉說明要查什麼、嚴重度如何判定、哪些情況不列為缺失、每個欄位怎麼寫。

<標準>
{_ref("jht-sop-standard.md")}
</標準>

<審核準則>
{_ref("review-criteria.md")}
</審核準則>

關於輸入與輸出：
- 文件以帶行號的文字提供，格式為 `[L0042 p3] 內容`；標有「表頭」的是頁首表格，標有「表格」的是表格的一列，欄位以 | 分隔。若同時附上 PDF 原檔，版面、表格欄位對應與流程圖以 PDF 為準，文字版用來標示行號與逐字引用。
- evidence 的 quote 必須是該行號內連續出現的原文，逐字照錄。系統會以程式比對每一段引文，比對不到的會被標記出來。
- chapters 依固定順序回報十個項目各一筆。
- findings 依嚴重度由高到低排列並編號 F01、F02…。文件品質好、缺失少時，如實回報少量缺失即可，不需要湊數。
- 全部以繁體中文撰寫；引文與建議的英文條文保留英文。"""


def review_user(doc_text: str, *, review_date: str, sop_type: str, notes: str, warnings: list[str], has_pdf: bool) -> str:
    parts = [f"審核日期：{review_date}"]
    parts.append(f"送審人員指定的 SOP 類別：{sop_type}" if sop_type and sop_type != "自動判定"
                 else "SOP 類別：未指定，請依內容判定。")
    if notes.strip():
        parts.append(f"送審人員補充說明：\n{notes.strip()}")
    if warnings:
        parts.append("文件擷取時的注意事項（這些是擷取限制，不是文件缺失）：\n" + "\n".join(f"- {w}" for w in warnings))
    if has_pdf:
        parts.append("已附上文件的 PDF 版本供你對照版面。")
    parts.append(f"<文件>\n{doc_text}\n</文件>")
    parts.append("請審核這份 SOP。")
    return "\n\n".join(parts)


def verify_system() -> str:
    return f"""你是喬山集團總部經營管理部的 SOP 審核主管，負責在審核報告發出前覆核初審人員提出的每一筆缺失。

報告一旦發出，撰寫單位會逐條回覆；一筆站不住腳的缺失會浪費雙方時間並削弱整份報告的可信度。你的工作是把每一筆缺失拿回原文重新檢驗，只留下經得起對質的。這不是重新審核，不需要新增缺失。

逐筆判定：
- **成立**：引文確實支持問題描述，問題依〈標準〉或〈審核準則〉確實是缺失，嚴重度符合校準。
- **調整**：缺失成立，但嚴重度或 must_fix 與校準不符。在 severity 與 must_fix 填入修正後的值，並在 reason 說明。
- **不成立**：有下列任一情況——引文沒有顯示所稱的問題；初審誤讀原文（例如把不同主體、不同類別 SOP 的規定當成矛盾）；屬於〈審核準則〉「不列為缺失的情況」；問題來自文件擷取的版面錯位而 PDF 原檔上並不存在；建議的內容文件其實已經有了；與另一筆缺失重複（保留較完整的那筆）。在 reason 寫出具體依據。

成立的缺失，severity 與 must_fix 照填初審的值。標記為「引文未通過程式比對」的缺失請特別對照 PDF 或文字版確認原文是否存在：原文確實存在（例如位於表格或流程圖）且問題成立者仍可判定成立。

<標準>
{_ref("jht-sop-standard.md")}
</標準>

<審核準則>
{_ref("review-criteria.md")}
</審核準則>"""


def verify_user(doc_text: str, findings_json: str, sop_type: str, has_pdf: bool) -> str:
    return "\n\n".join(filter(None, [
        f"初審判定的 SOP 類別：{sop_type}",
        "已附上文件的 PDF 版本供你對照版面。" if has_pdf else "",
        f"<文件>\n{doc_text}\n</文件>",
        f"<初審缺失>\n{findings_json}\n</初審缺失>",
        "請逐筆覆核，每筆缺失回覆一個 verdict。",
    ]))


def draft_system() -> str:
    return f"""你是喬山集團（Johnson Health Tech）總部經營管理部的 SOP 撰寫顧問，協助各單位同仁把手邊的參考資料整理成符合集團標準格式的 SOP 草稿。

使用你的人通常是第一次寫 SOP 的一般同仁：他們熟悉自己的業務，但不熟悉集團的 SOP 格式與審核標準。你產出的草稿會直接套入集團標準範本成為 Word 檔，由他們補齊待確認事項後送總部經管審核。所以草稿要做到兩件事：把參考資料中已有的內容忠實、完整地整理進正確的章節；把資料中沒有、但 SOP 必須有的內容清楚標示為待確認，讓撰寫人知道還要去問誰、補什麼。草稿裡每一個期限、門檻、核准者都會被當成制度執行，因此不可出現參考資料與〈標準〉都沒有依據的實質規定。

<標準>
{_ref("jht-sop-standard.md")}
</標準>

<撰寫準則>
{_ref("drafting-guide.md")}
</撰寫準則>

關於輸出：
- 每一章以條文清單回傳，number 為條號（如 2.1、5.1.1），第 6 章以流程步驟表回傳。
- Critical SOP 每條的 en 與 zh 都要寫；Local SOP 的 en 留空字串。
- 待確認的內容在條文中以【待確認：…】佔位，並逐項列入 open_questions。
- source_summary 向撰寫人說明：草稿用了參考資料的哪些部分、哪些章節因資料不足而留待確認。"""


def draft_user(*, title: str, sop_type: str, department: str, language_note: str, instructions: str, references: list[tuple[str, str]]) -> str:
    parts = ["請依下列資訊與參考資料撰寫 SOP 草稿。", f"SOP 主題：{title}", f"SOP 類別：{sop_type}"]
    if department.strip():
        parts.append(f"制定單位：{department.strip()}")
    if language_note:
        parts.append(language_note)
    if instructions.strip():
        parts.append(f"撰寫人的補充說明與要求：\n{instructions.strip()}")
    for name, text in references:
        parts.append(f"<參考資料 名稱=\"{name}\">\n{text}\n</參考資料>")
    if not references:
        parts.append("（撰寫人未提供參考資料檔案，僅有上述說明。請依說明撰寫，其餘必要內容以待確認標示。）")
    return "\n\n".join(parts)
