from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot_utils import build_saved_message, build_start_message, is_command
from app.config import Settings
from app.google_docs_utils import GoogleDocsUtils, GoogleSheetConfig
from app.llm_clients import ModelValidationError, TransientProviderError, build_llm_router
from app.proxy_utils import build_aiogram_session
from app.queue_store import QueueStore

MSK = ZoneInfo("Europe/Moscow")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _pipeline_backoff_seconds(attempt: int, base_seconds: int, max_seconds: int) -> int:
    return min(max_seconds, base_seconds * (2**attempt))


async def run_queue_worker(
    bot: Bot,
    settings: Settings,
    docs: GoogleDocsUtils,
    llm_router,
    queue: QueueStore,
    logger: logging.Logger,
) -> None:
    while True:
        item = await queue.fetch_due()
        if item is None:
            await asyncio.sleep(settings.queue_poll_interval_seconds)
            continue

        await queue.mark_processing(item.row_id)
        logger.info(
            "Processing queue item row_id=%s chat_id=%s message_id=%s attempts=%s",
            item.row_id,
            item.chat_id,
            item.message_id,
            item.attempts,
        )

        try:
            now_msk = datetime.now(MSK)
            expense = await llm_router.recognize(text=item.text, now_msk=now_msk)
            saved_at = datetime.now(MSK)
            inserted = await docs.append_expense(
                expense=expense,
                created_at_msk=saved_at,
                chat_id=item.chat_id,
                message_id=item.message_id,
            )
            await queue.mark_done(item.row_id)

            try:
                if inserted:
                    await bot.send_message(
                        chat_id=item.chat_id,
                        text=build_saved_message(expense, saved_at),
                    )
                else:
                    await bot.send_message(
                        chat_id=item.chat_id,
                        text="<b>Уже записано ранее</b>\nПо этому сообщению строка уже есть в таблице.",
                    )
            except Exception as notify_exc:  # noqa: BLE001
                logger.warning(
                    "Processed queue item but failed to notify chat_id=%s: %s",
                    item.chat_id,
                    notify_exc,
                )
        except ModelValidationError as exc:
            delay = _pipeline_backoff_seconds(
                item.attempts,
                settings.retry_backoff_base_seconds,
                settings.pipeline_retry_max_backoff_seconds,
            )
            await queue.schedule_retry(item.row_id, delay, str(exc))
            logger.warning(
                "Queue item model validation retry row_id=%s delay=%ss error=%s",
                item.row_id,
                delay,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            delay = _pipeline_backoff_seconds(
                item.attempts,
                settings.retry_backoff_base_seconds,
                settings.pipeline_retry_max_backoff_seconds,
            )
            await queue.schedule_retry(item.row_id, delay, str(exc))
            logger.warning(
                "Queue item retry scheduled row_id=%s delay=%ss error=%s transient=%s",
                item.row_id,
                delay,
                exc,
                isinstance(exc, TransientProviderError),
            )


async def run_bot() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    logger = logging.getLogger("tgbot-finances")

    os.makedirs(os.path.dirname(settings.queue_db_path) or ".", exist_ok=True)

    llm_router = build_llm_router(settings)
    docs = GoogleDocsUtils(
        GoogleSheetConfig(
            sheet_url=settings.google_sheet_url,
            worksheet_name=settings.google_worksheet_name,
            service_account_file=settings.google_service_account_file,
            append_retries=settings.google_append_retries,
            retry_backoff_base_seconds=settings.retry_backoff_base_seconds,
        )
    )
    await docs.ensure_headers()

    queue = QueueStore(settings.queue_db_path)
    await queue.init()

    bot = Bot(
        token=settings.telegram_bot_token,
        session=build_aiogram_session(settings.outbound_proxy),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(build_start_message())

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(build_start_message())

    @router.message(Command("ping"))
    async def on_ping(message: Message) -> None:
        await message.answer("pong")

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        if not settings.allow_group_chats and message.chat.type != "private":
            return

        chat_id = message.chat.id
        if settings.allowed_chat_ids and chat_id not in settings.allowed_chat_ids:
            return

        text = (message.text or "").strip()
        if not text:
            return

        if is_command(text):
            await message.answer("Команда не поддерживается. Используй /help или отправь расход текстом.")
            return

        enqueued = await queue.enqueue(chat_id=chat_id, message_id=message.message_id, text=text)
        if enqueued:
            await message.answer("<b>Принял сообщение</b>\nПоставил в очередь на обработку.")
        else:
            await message.answer("<b>Сообщение уже в обработке</b>")

    dp.include_router(router)

    if settings.outbound_proxy:
        logger.info("Bot started in polling mode via proxy")
    else:
        logger.info("Bot started in polling mode")

    worker_task = asyncio.create_task(run_queue_worker(bot, settings, docs, llm_router, queue, logger))
    try:
        await dp.start_polling(bot, drop_pending_updates=False)
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
        await bot.session.close()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
