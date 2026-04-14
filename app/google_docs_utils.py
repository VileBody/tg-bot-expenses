from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import gspread
from gspread.exceptions import WorksheetNotFound

from app.schemas import ExpenseRecord

HEADERS: Final[list[str]] = [
    "created_date_msk",
    "created_time_msk",
    "expense_date",
    "amount",
    "currency",
    "category",
    "description",
    "source_text",
    "llm_provider",
    "llm_model",
    "confidence",
    "tg_message_key",
    "tg_chat_id",
    "tg_message_id",
]


def _backoff_seconds(attempt_index: int, base_seconds: int) -> int:
    return base_seconds * (2**attempt_index)


def _looks_retryable_sheet_error(exc: Exception) -> bool:
    text = str(exc).lower()
    retryable_tokens = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "rate limit",
        "resource_exhausted",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
    )
    return any(token in text for token in retryable_tokens)


@dataclass(slots=True)
class GoogleSheetConfig:
    sheet_url: str
    worksheet_name: str
    service_account_file: str
    append_retries: int
    retry_backoff_base_seconds: int


class GoogleDocsUtils:
    """Google Sheets helper (intentionally named docs utils per task request)."""

    def __init__(self, config: GoogleSheetConfig) -> None:
        self.config = config
        self._worksheet = None
        self._lock = asyncio.Lock()

    def _get_worksheet_sync(self):
        if self._worksheet is not None:
            return self._worksheet

        client = gspread.service_account(filename=self.config.service_account_file)
        spreadsheet = client.open_by_url(self.config.sheet_url)

        try:
            worksheet = spreadsheet.worksheet(self.config.worksheet_name)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=self.config.worksheet_name, rows=2000, cols=20)

        self._worksheet = worksheet
        return worksheet

    def _ensure_headers_sync(self) -> None:
        worksheet = self._get_worksheet_sync()
        first_row = worksheet.row_values(1)

        if not first_row:
            worksheet.update("A1", [HEADERS], value_input_option="RAW")
            return

        normalized = [cell.strip().lower() for cell in first_row[: len(HEADERS)]]
        expected = [header.lower() for header in HEADERS]

        if normalized == expected:
            return

        has_data_in_row1 = any(cell.strip() for cell in first_row)
        if has_data_in_row1:
            worksheet.insert_row(HEADERS, 1, value_input_option="RAW")
        else:
            worksheet.update("A1", [HEADERS], value_input_option="RAW")

    def _find_col_index_sync(self, header_name: str) -> int | None:
        worksheet = self._get_worksheet_sync()
        first_row = worksheet.row_values(1)
        for idx, value in enumerate(first_row, start=1):
            if value.strip().lower() == header_name.lower():
                return idx
        return None

    def _exists_message_key_sync(self, message_key: str) -> bool:
        worksheet = self._get_worksheet_sync()
        message_key_col = self._find_col_index_sync("tg_message_key")
        if message_key_col is None:
            return False
        # Compatible with gspread versions where CellNotFound may not exist.
        column_values = worksheet.col_values(message_key_col)
        return message_key in column_values

    def _append_expense_sync(
        self,
        expense: ExpenseRecord,
        created_at_msk: datetime,
        message_key: str,
        chat_id: int,
        message_id: int,
    ) -> bool:
        worksheet = self._get_worksheet_sync()

        if self._exists_message_key_sync(message_key):
            return False

        row = [
            created_at_msk.strftime("%Y-%m-%d"),
            created_at_msk.strftime("%H:%M:%S"),
            expense.expense_date,
            expense.amount,
            expense.currency,
            expense.category,
            expense.description,
            expense.source_text,
            expense.llm_provider,
            expense.llm_model,
            "" if expense.confidence is None else round(expense.confidence, 3),
            message_key,
            str(chat_id),
            str(message_id),
        ]
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        return True

    async def ensure_headers(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._ensure_headers_sync)

    async def append_expense(
        self,
        expense: ExpenseRecord,
        created_at_msk: datetime,
        *,
        chat_id: int,
        message_id: int,
    ) -> bool:
        message_key = f"{chat_id}:{message_id}"

        async with self._lock:
            attempt = 0
            while True:
                try:
                    return await asyncio.to_thread(
                        self._append_expense_sync,
                        expense,
                        created_at_msk,
                        message_key,
                        chat_id,
                        message_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    if attempt >= self.config.append_retries or not _looks_retryable_sheet_error(exc):
                        raise
                    delay = _backoff_seconds(attempt, self.config.retry_backoff_base_seconds)
                    attempt += 1
                    await asyncio.sleep(delay)
