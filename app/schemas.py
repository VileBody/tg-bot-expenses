from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _to_float(raw: Any) -> float:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("amount is empty")

    cleaned = re.sub(r"[^0-9,.-]", "", text)
    if cleaned.count(",") > 0 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(",") > 0 and cleaned.count(".") > 0:
        cleaned = cleaned.replace(",", "")

    value = float(cleaned)
    if value <= 0:
        raise ValueError("amount must be > 0")
    return round(value, 2)


@dataclass(slots=True)
class ExpenseRecord:
    amount: float
    currency: str
    category: str
    description: str
    expense_date: str
    confidence: float | None
    source_text: str
    llm_provider: str
    llm_model: str


def normalize_expense_payload(
    payload: dict[str, Any],
    *,
    source_text: str,
    llm_provider: str,
    llm_model: str,
    fallback_date: str,
) -> ExpenseRecord:
    amount = _to_float(payload.get("amount"))

    category = str(payload.get("category") or "").strip()
    if not category:
        raise ValueError("category is empty")

    description = str(payload.get("description") or "").strip() or source_text.strip()
    currency = str(payload.get("currency") or "RUB").strip().upper()[:8]

    expense_date = str(payload.get("expense_date") or fallback_date).strip()
    if not expense_date:
        expense_date = fallback_date

    raw_confidence = payload.get("confidence")
    confidence: float | None = None
    if raw_confidence is not None and str(raw_confidence).strip() != "":
        try:
            confidence = max(0.0, min(1.0, float(raw_confidence)))
        except (TypeError, ValueError):
            confidence = None

    return ExpenseRecord(
        amount=amount,
        currency=currency,
        category=category,
        description=description,
        expense_date=expense_date,
        confidence=confidence,
        source_text=source_text.strip(),
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
