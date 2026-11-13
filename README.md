# 喬山 SOP 自評與審核系統 v2（Claude 版）

以 Claude 重新製作的 SOP 系統，包含三個功能與一套可在 Claude Code 直接使用的 skill：

| 功能 | 對象 | 輸出 |
|---|---|---|
| ① 參考資料生成 SOP 草稿 | 一般同仁 | 套用集團標準範本的 Word 草稿＋待確認事項清單 |
| ② SOP 快速自評 | 一般同仁 | 畫面上的問題清單與可直接採用的修正條文＋Word |
| ③ SOP 審核工作台 | 總部經管 | Word 審核報告＋Excel 缺失追蹤表 |

> 本資料夾是獨立的新版本，不依賴 repo 內舊的 `.agents/skills/jht-sop-review`（Codex 版），也未參考原網頁系統
> （`jht-sop-review` repo 的 `deploy/render-sop-v1`）的程式碼——製作時無法存取該 repo。

## 相對於舊版改了什麼

| 舊版（Codex skill）的問題 | v2 的做法 |
|---|---|
| 規則只有通用的一行描述，沒有公司實際規定 | 把 TT-QP-CO-216 與 8.1 標準範本整理成 `jht-sop-standard.md`：四種 SOP 類別、簽核權限表、語言要求、時限、表頭九欄、八章各自該寫與不該寫什麼、章節間的對應關係 |
| 表格被拆成單一儲存格，簽核權限表與程序表的列欄關係消失 | 表格以「列」輸出；PDF 另外把原檔直接交給 Claude 看版面 |
| PDF 每頁重複的表頭混入內文；Word 自動編號抓不到條號 | 重複表頭只保留一次；還原 Word 自動編號為實際條號 |
| 十二類一次審完，沒有嚴重度校準與判斷指引 | `review-criteria.md` 提供審核步驟、十個面向、嚴重度校準表、每個欄位的寫法 |
| 容易出現指不到原文、或把擷取雜訊當缺失的意見 | 明列「不列為缺失的情況」；每段引文由程式逐字比對；正式審核加一輪覆核，剔除站不住腳的意見 |
| 送簽結論與分數由模型自由發揮 | 送簽建議與參考分數由程式依固定規則計算（`sopkit/verify.py`） |
| 沒有草稿生成的規範，容易編造期限與核准者 | `drafting-guide.md` 規定內容來源：參考資料沒有的實質內容一律標【待確認】並列成清單；輸出直接填入集團範本 |
| 串接 ChatGPT API，自由文字輸出 | 串接 Claude API（預設 `claude-opus-5-5`），結構化輸出（JSON Schema）、串流、系統提示快取 |

## 資料夾結構

```
render-sop-v2_claude/
├── app/                      網頁系統（FastAPI）
│   ├── main.py               API、背景工作、帳密保護
│   ├── services.py           審核與草稿兩條流程
│   ├── llm.py                Claude API 呼叫
│   ├── prompts.py            提示詞（讀取 skill 的 references）
│   └── static/index.html     前端頁面
├── skills/jht-sop/           Claude skill（網頁系統與 Claude Code 共用同一份準則）
│   ├── SKILL.md
│   ├── references/           公司標準、審核準則、撰寫準則  ← 調整審核行為改這裡
│   ├── assets/sop-template.docx
│   └── scripts/              擷取、引文驗證、Word／Excel 輸出（不呼叫模型）
├── reference-docs/           原始參考文件（TT-QP-CO-216、標準範本）
├── tests/                    離線測試
└── render.yaml               Render 部署設定
```

## 本機執行

```bash
cd render-sop-v2_claude
pip install -r requirements.txt
export ANTHROPIC_API_KEY=你的金鑰
export APP_PASSWORD=自訂密碼
uvicorn app.main:app --port 8000
```

開啟 http://localhost:8000 ，帳號 `sop`、密碼為 `APP_PASSWORD`。

## 部署到 Render

1. 在 Render 建立 Web Service，Root Directory 設為 `render-sop-v2_claude`（或使用 `render.yaml` Blueprint）。
2. Build Command：`pip install -r requirements.txt`
3. Start Command：`uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
4. 在 Environment 設定 `ANTHROPIC_API_KEY` 與 `APP_PASSWORD`。

注意事項：
- 工作進度與輸出檔存在記憶體與暫存目錄，服務重啟後即消失，請維持單一 instance、單一 worker，報告產生後請立即下載。
- Render 的 Python 環境沒有 LibreOffice，上傳 Word 檔時只能以文字審核（沒有頁碼與版面）。**含流程圖或複雜表格的 SOP 建議上傳 PDF**，Claude 可直接看到版面。舊版 `.doc` 需另存為 `.docx` 或 PDF。

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | （必填） | Claude API 金鑰 |
| `APP_PASSWORD` | 無 | 設定後啟用帳密保護；**處理公司文件請務必設定** |
| `APP_USER` | `sop` | 登入帳號 |
| `CLAUDE_MODEL` | `claude-opus-5-5` | 使用的模型 |
| `CLAUDE_EFFORT_REVIEW` / `_VERIFY` / `_DRAFT` | `high` / `medium` / `high` | 各階段的推理深度（low–max），越高越仔細也越慢、越貴 |
| `REVIEW_VERIFY_PASS` | `1` | 正式審核是否執行第二輪覆核；設 `0` 可省約一半費用與時間 |
| `CLAUDE_REFUSAL_FALLBACK` | `1` | 模型拒答時由 API 自動改用備援模型 |
| `CLAUDE_MAX_TOKENS` | `48000` | 單次輸出上限 |
| `MAX_UPLOAD_MB` | `25` | 單檔上傳上限 |
| `JOB_TTL_HOURS` | `6` | 工作結果保留時數 |

## 在 Claude Code 使用 skill（不經網頁）

把 `skills/jht-sop` 複製到專案的 `.claude/skills/jht-sop`，之後直接對 Claude 說「審核這份 SOP」或「用這些資料寫一份 SOP 草稿」即可。

## 調整審核與撰寫行為

所有判斷準則都是 `skills/jht-sop/references/` 下的 Markdown，改完重啟服務即生效，不需要改程式：

- 公司規定有更新（例如 TT-QP-CO-216 改版）→ 改 `jht-sop-standard.md`
- 某類意見太多、太少或嚴重度不對 → 改 `review-criteria.md` 的面向表、嚴重度校準、「不列為缺失的情況」
- 草稿寫法要調整 → 改 `drafting-guide.md`

## 測試

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

測試以假的模型回應驗證擷取、引文比對、結論規則、Word／Excel 輸出與 API 流程，不會呼叫 Claude。

## 已知限制

- 掃描檔（影像 PDF）無法擷取文字，需提供文字版。
- Word 檔內的流程圖、文字方塊、圖片中的文字無法擷取，報告會標示須人工覆核。
- 審核意見是送簽前的建議，仍須由審核人員確認，不代表文件已核准。
