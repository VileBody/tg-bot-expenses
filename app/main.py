from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events

from app.bot_utils import build_error_message, build_saved_message, build_start_message, is_command
from app.config import Settings
from app.google_docs_utils import GoogleDocsUtils, GoogleSheetConfig
from app.llm_clients import ModelValidationError, TransientProviderError, build_llm_router
from app.proxy_utils import build_telethon_proxy
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
    client: TelegramClient,
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

            if inserted:
                await client.send_message(
                    entity=item.chat_id,
                    message=build_saved_message(expense, saved_at),
                    parse_mode="html",
                )
            else:
                await client.send_message(
                    entity=item.chat_id,
                    message="<b>Уже записано ранее</b>\nПо этому сообщению строка уже есть в таблице.",
                    parse_mode="html",
                )
        except ModelValidationError as exc:
            await queue.mark_failed(item.row_id, str(exc))
            logger.warning(
                "Queue item failed by model validation row_id=%s chat_id=%s: %s",
                item.row_id,
                item.chat_id,
                exc,
            )
            await client.send_message(
                entity=item.chat_id,
                message=build_error_message(),
                parse_mode="html",
            )
        except Exception as exc:  # noqa: BLE001
            should_retry = isinstance(exc, TransientProviderError)
            if not should_retry:
                # Keep messages safe by retrying unknown runtime errors as well.
                should_retry = True

            if should_retry:
                delay = _pipeline_backoff_seconds(
                    item.attempts,
                    settings.retry_backoff_base_seconds,
                    settings.pipeline_retry_max_backoff_seconds,
                )
                await queue.schedule_retry(item.row_id, delay, str(exc))
                logger.warning(
                    "Queue item retry scheduled row_id=%s delay=%ss error=%s",
                    item.row_id,
                    delay,
                    exc,
                )
            else:
                await queue.mark_failed(item.row_id, str(exc))
                logger.exception(
                    "Queue item failed permanently row_id=%s chat_id=%s",
                    item.row_id,
                    item.chat_id,
                )


async def run_bot() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    logger = logging.getLogger("tgbot-finances")
    telethon_proxy = build_telethon_proxy(settings.outbound_proxy)

    os.makedirs(os.path.dirname(settings.telethon_session_path) or ".", exist_ok=True)
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

    client = TelegramClient(
        settings.telethon_session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=telethon_proxy,
        catch_up=True,
    )

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event):
        if event.out:
            return

        if not settings.allow_group_chats and not event.is_private:
            return

        chat_id = event.chat_id
        if chat_id is None:
            return

        if settings.allowed_chat_ids and chat_id not in settings.allowed_chat_ids:
            return

        text = (event.raw_text or "").strip()
        if not text:
            return

        if text.startswith("/start") or text.startswith("/help"):
            await event.respond(build_start_message(), parse_mode="html")
            return

        if text.startswith("/ping"):
            await event.respond("pong")
            return

        if is_command(text):
            await event.respond(
                "Команда не поддерживается. Используй /help или отправь расход текстом.",
                parse_mode="html",
            )
            return

        if event.id is None:
            return

        enqueued = await queue.enqueue(chat_id=chat_id, message_id=event.id, text=text)
        if enqueued:
            await event.respond(
                "<b>Принял сообщение</b>\nПоставил в очередь на обработку.",
                parse_mode="html",
            )
        else:
            await event.respond(
                "<b>Сообщение уже в обработке</b>",
                parse_mode="html",
            )

    await client.start(bot_token=settings.telegram_bot_token)
    if telethon_proxy:
        logger.info("Bot started in polling mode via proxy")
    else:
        logger.info("Bot started in polling mode")

    worker_task = asyncio.create_task(run_queue_worker(client, settings, docs, llm_router, queue, logger))
    try:
        await client.run_until_disconnected()
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
