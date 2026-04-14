from __future__ import annotations

import html
from datetime import datetime

from app.schemas import ExpenseRecord


def _money(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ")


def build_start_message() -> str:
    return (
        "<b>Бот учета расходов запущен</b>\\n\\n"
        "Пришли сообщение в свободной форме, например:\\n"
        "• Обед с партнером 1500\\n"
        "• Такси 800\\n"
        "• Купили канцтовары на 5000"
    )


def build_saved_message(expense: ExpenseRecord, created_at: datetime) -> str:
    category = html.escape(expense.category)
    description = html.escape(expense.description)

    return (
        "<b>Записал расход в таблицу</b>\\n\\n"
        f"• <b>Сумма:</b> {_money(expense.amount)} {expense.currency}\\n"
        f"• <b>Категория:</b> {category}\\n"
        f"• <b>Описание:</b> {description}\\n"
        f"• <b>Дата расхода:</b> {expense.expense_date}\\n"
        f"• <b>Дата добавления (МСК):</b> {created_at.strftime('%Y-%m-%d')}\\n"
        f"• <b>Время добавления (МСК):</b> {created_at.strftime('%H:%M:%S')}\\n\\n"
        "<b>Внесенные поля:</b>\\n"
        "• сумма\\n"
        "• категория\\n"
        "• описание\\n"
        "• дата/время\\n"
        "• исходный текст"
    )


def build_error_message() -> str:
    return (
        "<b>Не смог обработать сообщение</b>\\n"
        "Попробуй написать расход чуть точнее: сумма + категория/контекст."
    )


def is_command(text: str) -> bool:
    return text.strip().startswith("/")
