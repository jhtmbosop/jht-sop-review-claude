"""Claude API 呼叫：串流 + 結構化輸出（JSON Schema）。"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import anthropic
from pydantic import BaseModel

log = logging.getLogger("sop.llm")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT_REVIEW = os.environ.get("CLAUDE_EFFORT_REVIEW", "high")
EFFORT_VERIFY = os.environ.get("CLAUDE_EFFORT_VERIFY", "medium")
EFFORT_DRAFT = os.environ.get("CLAUDE_EFFORT_DRAFT", "high")
USE_FALLBACK = os.environ.get("CLAUDE_REFUSAL_FALLBACK", "1") == "1"
MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "48000"))
PDF_MAX_BYTES = 30 * 1024 * 1024

_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    """可直接顯示給使用者的錯誤訊息。"""


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # 憑證由環境變數 ANTHROPIC_API_KEY 提供；單次請求最長 20 分鐘
        _client = anthropic.Anthropic(timeout=1200.0, max_retries=2)
    return _client


def pdf_block(path: Path) -> dict | None:
    data = path.read_bytes()
    if len(data) > PDF_MAX_BYTES:
        return None
    return {"type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(data).decode()}}


def _stream(kwargs: dict, fallback: bool):
    if fallback:
        # 模型因安全分類拒答時，由 API 端自動改用備援模型重跑同一請求
        with client().beta.messages.stream(**kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default") as s:
            return s.get_final_message()
    with client().messages.stream(**kwargs) as s:
        return s.get_final_message()


def call_structured(system: str, content: list[dict] | str, schema: type[BaseModel], *, effort: str) -> tuple[BaseModel, dict]:
    """呼叫 Claude 並回傳通過 schema 驗證的結果與用量。"""
    kwargs = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # 系統提示（公司標準＋準則）每次相同，設定快取以降低重複呼叫成本
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}},
    )
    try:
        try:
            msg = _stream(kwargs, USE_FALLBACK)
        except anthropic.BadRequestError as e:
            if not (USE_FALLBACK and "fallback" in str(e).lower()):
                raise
            log.warning("此模型或帳號不支援 server-side fallback，改以一般請求重試：%s", e)
            msg = _stream(kwargs, False)
    except TypeError as e:
        if "authentication" in str(e).lower():
            raise LLMError("伺服器尚未設定 Claude API 金鑰（環境變數 ANTHROPIC_API_KEY）。") from e
        raise
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude API 金鑰無效或未設定（請檢查 ANTHROPIC_API_KEY）。") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError("此 API 金鑰沒有使用該模型的權限。") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"找不到模型 {MODEL}，請檢查 CLAUDE_MODEL 設定。") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Claude API 已達速率上限，請稍後再試。") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"Claude API 拒絕此請求：{e.message}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API 發生錯誤（{e.status_code}），請稍後再試。") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("無法連線到 Claude API，請檢查網路。") from e

    if msg.stop_reason == "refusal":
        raise LLMError("模型拒絕處理這份文件，請確認文件內容後再試。")
    if msg.stop_reason == "max_tokens":
        raise LLMError("模型輸出超過長度上限而被截斷。請調高 CLAUDE_MAX_TOKENS，或將文件拆分後分次處理。")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        parsed = schema.model_validate_json(text)
    except Exception as e:
        raise LLMError("模型回傳的結果格式不完整，請重試。") from e
    u = msg.usage
    usage = {"model": msg.model, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
             "request_id": getattr(msg, "_request_id", None)}
    log.info("claude call ok %s", usage)
    return parsed, usage
