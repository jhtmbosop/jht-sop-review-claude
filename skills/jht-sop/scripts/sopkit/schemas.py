"""模型輸出的資料結構（同時作為 Claude 結構化輸出的 JSON Schema）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SopType = Literal["Group Critical SOP", "Affiliate Critical SOP", "HQ Local SOP", "Affiliate Local SOP", "無法判定"]
Category = Literal["格式與架構", "文件管制", "中英文一致性", "範圍與定義", "權責", "程序可執行性",
                   "管制重點與內控", "跨條文一致性", "用語與明確性", "時效與引用文件"]
Severity = Literal["重大", "高", "中", "低"]
Basis = Literal["文件確認事實", "審核推論", "待向原單位確認"]
ChapterItem = Literal["表頭", "1 Purpose 目的", "2 Scope 範圍", "3 SOP Maintenance 維護責任", "4 Definition 定義",
                      "5 Responsibility 權責", "6 Process 程序", "7 Control 管制重點", "8 Documents 使用文件",
                      "SOP 修改對照表"]
CATEGORIES = list(Category.__args__)
SEVERITIES = list(Severity.__args__)
CHAPTER_ITEMS = list(ChapterItem.__args__)


# ------------------------------------------------------------ 審核

class Evidence(BaseModel):
    line_id: str = Field(description="引文所在行號，例如 L0042")
    quote: str = Field(description="該行中的連續原文，逐字照錄，不改寫、不翻譯、不加刪節號")


class Finding(BaseModel):
    id: str = Field(description="F01、F02… 依嚴重度由高到低編號")
    category: Category
    severity: Severity
    clause: str = Field(description="相關條號，例如 4.3.2、7.2；表頭寫「表頭」")
    title: str = Field(description="一句話標題，15 字以內")
    evidence: list[Evidence]
    problem: str
    impact: str
    recommendation: str
    suggested_text_zh: str = Field(description="可直接貼上的修正後中文條文；不適用則空字串")
    suggested_text_en: str = Field(description="對應的英文條文；Local SOP 或不適用則空字串")
    basis: Basis
    must_fix: bool


class ChapterCheck(BaseModel):
    item: ChapterItem
    status: Literal["符合", "不完整", "缺漏"]
    note: str = Field(description="不完整或缺漏時說明缺什麼；符合可留空")


class DocMeta(BaseModel):
    title_zh: str
    title_en: str
    control_number: str
    rev: str
    effective_date: str
    approved_by: str
    prepared_by: str
    sop_type: SopType
    sop_type_reason: str
    language: Literal["中英對照", "僅中文", "僅英文", "其他"]


class ReviewResult(BaseModel):
    meta: DocMeta
    chapters: list[ChapterCheck] = Field(description="十個項目各一筆，依固定順序")
    findings: list[Finding]
    strengths: list[str]
    summary: str
    priorities: list[str]


class FindingVerdict(BaseModel):
    id: str
    verdict: Literal["成立", "調整", "不成立"]
    reason: str = Field(description="調整或不成立時說明依據；成立可留空")
    severity: Severity = Field(description="覆核後的嚴重度")
    must_fix: bool


class VerifyResult(BaseModel):
    verdicts: list[FindingVerdict]


# ------------------------------------------------------------ 草稿

class Clause(BaseModel):
    number: str = Field(description="條號，例如 2.1；該章只有一段時可留空")
    en: str = Field(description="英文條文；Local SOP 留空字串")
    zh: str = Field(description="繁體中文條文")


class ProcessStep(BaseModel):
    process: str = Field(description="步驟名稱（流程欄），簡短，例如「提出申請 Submit request」")
    instruction_en: str
    instruction_zh: str
    control_doc: str = Field(description="此步驟使用的表單或系統；無則空字串")
    responsibility: str = Field(description="權責單位")


class ProcessFlow(BaseModel):
    number: str = Field(description="例如 6.1")
    title_en: str
    title_zh: str
    steps: list[ProcessStep]


class OpenQuestion(BaseModel):
    clause: str
    question: str = Field(description="需要原單位補充或決定的事項")
    reason: str = Field(description="為什麼需要：參考資料未提供／參考資料互相矛盾／其他")


class SopDraft(BaseModel):
    title_en: str = Field(description="以 SOP for 開頭")
    title_zh: str
    sop_type: SopType
    purpose: list[Clause]
    scope: list[Clause]
    maintenance: list[Clause]
    definition: list[Clause]
    responsibility: list[Clause]
    process: list[ProcessFlow]
    control: list[Clause]
    documents: list[Clause]
    open_questions: list[OpenQuestion]
    source_summary: str = Field(description="兩三句說明草稿內容取自參考資料的哪些部分，以及哪些章節因資料不足而以待確認佔位")
