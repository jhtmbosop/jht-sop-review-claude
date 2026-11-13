---
name: jht-sop
description: 喬山集團 SOP 的撰寫與審核。依 TT-QP-CO-216 與集團 SOP 標準範本，(1) 把參考資料整理成標準格式的 SOP 草稿 Word 檔，(2) 審核 DOCX／PDF 的 SOP，產出附逐字引文與修正條文的 Word 審核報告及 Excel 缺失追蹤表。使用者提到喬山 SOP、SOP 草稿、SOP 自評、SOP 審核、送簽前檢查、Group Critical／Affiliate Critical／Local SOP 時使用。
---

# 喬山 SOP 撰寫與審核

這個 skill 讓你以總部經營管理部資深審核人員的標準處理喬山集團 SOP。判斷依據都在 `references/`，Word 輸出由 `scripts/sop.py` 處理；你負責閱讀、判斷與撰寫。

開始任何工作前先讀 `references/jht-sop-standard.md`（公司對 SOP 的明文要求）。它是唯一可以直接引用的制度依據；其中沒有的金額門檻、核決權限、部門職掌，都要向使用者確認，不要推定。

首次使用先安裝套件：`pip install -r scripts/requirements.txt`

## 審核 SOP

1. 讀 `references/review-criteria.md`。
2. 擷取文件，取得帶行號的全文：
   ```bash
   python scripts/sop.py extract <SOP檔> --json work/source.json
   ```
   輸出格式為 `[L0042 p3] 內容`，「表頭」是頁首表格，「表格」是表格的一列。檔案是 PDF 時，同時直接閱讀 PDF 原檔對照版面——流程圖與多欄表格在文字版會錯位，以原檔為準。
3. 依準則的審核步驟完成審核，把結果寫成 `work/review.json`，結構見 `scripts/sopkit/schemas.py` 的 `ReviewResult`。每段 evidence 的 quote 必須是該行號內連續的原文。
4. 產生報告：
   ```bash
   python scripts/sop.py review <SOP檔> work/review.json --out result/ [--mode self]
   ```
   腳本會逐字比對每段引文。出現「引文未通過逐字比對」時，回頭修正 `review.json` 的引文或行號後重跑，直到全部通過；確實位於流程圖等無法擷取處的引文，保留並在報告中由系統標示。
5. 送出報告前，把每筆缺失拿回原文再檢查一次：引文是否真的顯示所稱的問題、是否屬於準則「不列為缺失的情況」、嚴重度是否符合校準。站不住腳的在 `review.json` 把該筆加上 `"status": "不成立"` 與 `"verify_note"`，或直接刪除。
6. 向使用者回報送簽建議、各嚴重度數量、優先處理事項與輸出檔路徑。送簽建議由腳本依固定規則計算（`sopkit/verify.py` 的 `conclude`），以腳本輸出為準。

`--mode self` 用於撰寫人送審前的自評，報告不含原單位回覆欄。

## 撰寫 SOP 草稿

1. 讀 `references/drafting-guide.md`。
2. 向使用者確認 SOP 主題與類別（四種之一，決定是否需要中英對照），並讀完他提供的所有參考資料。參考資料是檔案時可用 `sop.py extract` 取得文字。
3. 撰寫草稿並存成 `work/draft.json`，結構見 `schemas.py` 的 `SopDraft`。參考資料沒有的實質內容以【待確認：…】佔位並列入 `open_questions`。
4. 套入集團標準範本：
   ```bash
   python scripts/sop.py draft work/draft.json --out result/<名稱>_草稿.docx \
     --control-number <編號> --prepared-by <制定人> [--approved-by <核准人>]
   ```
5. 把待確認事項清單交給使用者，說明每項要向誰確認什麼。使用者想先自我檢查時，對產出的 Word 檔執行上面的審核流程（`--mode self`）。

## 檔案

- `references/jht-sop-standard.md`：SOP 類別、簽核權限表、語言要求、表頭與八大章節格式、章節對應關係。
- `references/review-criteria.md`：審核步驟、十個面向、嚴重度校準、缺失寫法、不列為缺失的情況。
- `references/drafting-guide.md`：草稿的內容來源規則與各章寫法。
- `assets/sop-template.docx`：集團 SOP 標準範本（8.1 Group SOP Standard Template）。
- `scripts/sop.py`：擷取、引文驗證、Word／Excel 輸出；不呼叫模型或網路。
