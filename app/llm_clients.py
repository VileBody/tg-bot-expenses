from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings
from app.proxy_utils import apply_outbound_proxy_environment
from app.schemas import ExpenseRecord, normalize_expense_payload

EXPENSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "amount": {
            "type": "number",
            "description": "Сумма расхода. Только число, без валютного символа.",
        },
        "currency": {
            "type": "string",
            "description": "Валюта (обычно RUB, USD, EUR).",
        },
        "category": {
            "type": "string",
            "description": "Краткая категория расхода: такси, еда, офис, подписки и т.д.",
        },
        "description": {
            "type": "string",
            "description": "Краткое описание расхода (1-2 фразы).",
        },
        "expense_date": {
            "type": "string",
            "description": "Дата расхода в формате YYYY-MM-DD.",
        },
        "confidence": {
            "type": "number",
            "description": "Оценка уверенности извлечения от 0 до 1.",
        },
    },
    "required": ["amount", "category", "description"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "Ты финансовый ассистент. Извлекай расход из текста пользователя. "
    "Если каких-то деталей нет, делай разумные предположения: валюту = RUB, категорию по смыслу, "
    "дату расхода = текущая дата (по Москве)."
)


class TransientProviderError(Exception):
    pass


class ModelValidationError(Exception):
    pass


def _backoff_seconds(attempt_index: int, base_seconds: int) -> int:
    return base_seconds * (2**attempt_index)


def _looks_transient(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(code, int) and code in {408, 429, 500, 502, 503, 504}:
        return True

    text = str(exc).lower()
    transient_tokens = (
        "429",
        "503",
        "rate limit",
        "resource_exhausted",
        "unavailable",
        "overloaded",
        "timeout",
        "timed out",
        "connection reset",
        "temporary",
    )
    return any(token in text for token in transient_tokens)


class GeminiExpenseClient:
    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        outbound_proxy: str | None,
        timeout_seconds: int,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        apply_outbound_proxy_environment(outbound_proxy)
        self.client = genai.Client(api_key=api_key)

    async def recognize_with_calling(self, text: str, now_msk: datetime) -> dict[str, Any]:
        prompt = (
            f"{SYSTEM_PROMPT}\\n"
            f"Текущая дата по Москве: {now_msk.strftime('%Y-%m-%d')}\\n"
            f"Текст пользователя: {text}"
        )

        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                        response_schema=EXPENSE_SCHEMA,
                    ),
                ),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TransientProviderError("Gemini timeout") from exc
        except Exception as exc:  # noqa: BLE001
            if _looks_transient(exc):
                raise TransientProviderError(str(exc)) from exc
            raise

        if not response.text:
            raise ModelValidationError("Gemini returned empty body")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ModelValidationError("Gemini returned invalid JSON") from exc


class LLMRouter:
    def __init__(
        self,
        client: GeminiExpenseClient,
        transient_retries: int,
        validation_retries: int,
        backoff_base_seconds: int,
    ) -> None:
        self.client = client
        self.transient_retries = transient_retries
        self.validation_retries = validation_retries
        self.backoff_base_seconds = backoff_base_seconds

    async def recognize(self, text: str, now_msk: datetime) -> ExpenseRecord:
        transient_attempt = 0
        validation_attempt = 0

        while True:
            try:
                payload = await self.client.recognize_with_calling(text=text, now_msk=now_msk)
                return normalize_expense_payload(
                    payload,
                    source_text=text,
                    llm_provider=self.client.provider_name,
                    llm_model=self.client.model,
                    fallback_date=now_msk.strftime("%Y-%m-%d"),
                )
            except ModelValidationError:
                if validation_attempt >= self.validation_retries:
                    raise
                delay = _backoff_seconds(validation_attempt, self.backoff_base_seconds)
                validation_attempt += 1
                await asyncio.sleep(delay)
            except ValueError as exc:
                # Normalization errors are also model output validation failures.
                if validation_attempt >= self.validation_retries:
                    raise ModelValidationError(str(exc)) from exc
                delay = _backoff_seconds(validation_attempt, self.backoff_base_seconds)
                validation_attempt += 1
                await asyncio.sleep(delay)
            except TransientProviderError:
                if transient_attempt >= self.transient_retries:
                    raise
                delay = _backoff_seconds(transient_attempt, self.backoff_base_seconds)
                transient_attempt += 1
                await asyncio.sleep(delay)


def build_llm_router(settings: Settings) -> LLMRouter:
    client = GeminiExpenseClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        outbound_proxy=settings.outbound_proxy,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    return LLMRouter(
        client=client,
        transient_retries=settings.gemini_transient_retries,
        validation_retries=settings.gemini_validation_retries,
        backoff_base_seconds=settings.retry_backoff_base_seconds,
    )
