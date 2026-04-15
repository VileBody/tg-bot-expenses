# tgbot-finances

Минималистичный Telegram-бот для учета расходов:
- Telegram Bot API polling (aiogram)
- Gemini SDK
- structured output (SO) для извлечения полей расхода
- append в Google Sheets с проверкой/добавлением хедеров
- дата и время записи в отдельных колонках (Europe/Moscow)
- retries/backoff (2,4,8,16 по умолчанию) для Gemini и Google Sheets
- защита от дублей записи по ключу `chat_id:message_id`
- локальная персистентная очередь (SQLite) для дообработки после сбоев

## Структура

- `app/main.py` - запуск бота и polling
- `app/llm_clients.py` - Gemini + метод `recognize_with_calling`
- `app/proxy_utils.py` - прокси для Bot API и Gemini
- `app/google_docs_utils.py` - утилиты Google Docs/Sheets
- `app/bot_utils.py` - форматирование ответов для Telegram
- `app/config.py` - чтение `.env`
- `app/queue_store.py` - персистентная очередь сообщений (SQLite)

## Что нужно подготовить

1. Telegram bot token (`@BotFather`)
2. Google Sheet URL
3. Service Account JSON файл Google (с доступом Editor к таблице)
4. Gemini API key
5. (Опционально) `OUTBOUND_PROXY` вида `http://user:pass@host:port`

## Быстрый старт в Docker

```bash
cp .env.example .env
mkdir -p secrets
# положите ключ Google сюда:
# secrets/google-service-account.json

docker compose up -d --build

docker compose logs -f bot
```

## Локальный запуск (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

## Локальный запуск (Windows PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.main
```

## Деплой на Ubuntu по SSH (ключ)

На сервере (один раз):

```bash
sudo mkdir -p /opt/tgbot-finances
sudo chown -R $USER:$USER /opt/tgbot-finances
cd /opt/tgbot-finances
```

### One-liner: закинуть `.env` на сервер

macOS/Linux:

```bash
scp -i ~/.ssh/id_ed25519 .env ubuntu@SERVER_IP:/opt/tgbot-finances/.env
```

Windows PowerShell:

```powershell
scp -i C:\keys\id_ed25519 .\.env ubuntu@SERVER_IP:/opt/tgbot-finances/.env
```

### Закинуть сервисный ключ Google

```bash
scp -i ~/.ssh/id_ed25519 secrets/google-service-account.json ubuntu@SERVER_IP:/opt/tgbot-finances/secrets/google-service-account.json
```

Windows PowerShell:

```powershell
scp -i C:\keys\id_ed25519 .\secrets\google-service-account.json ubuntu@SERVER_IP:/opt/tgbot-finances/secrets/google-service-account.json
```

### Запуск на сервере

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@SERVER_IP "cd /opt/tgbot-finances && docker compose up -d --build"
ssh -i ~/.ssh/id_ed25519 ubuntu@SERVER_IP "cd /opt/tgbot-finances && docker compose logs -f bot"
```

## Как работает запись в таблицу

При входящем сообщении:
1. сообщение кладется в локальную очередь SQLite
2. worker забирает задачу и отправляет текст в Gemini с SO/calling
3. перед append проверяется первая строка (хедеры)
4. добавляется новая строка с колонками:
   - `created_date_msk`
   - `created_time_msk`
   - `expense_date`
   - `amount`
   - `currency`
   - `category`
   - `description`
   - `source_text`
   - `llm_provider`
   - `llm_model`
   - `confidence`
   - `tg_message_key`
   - `tg_chat_id`
   - `tg_message_id`

## Важные примечания

- Если задан `OUTBOUND_PROXY`, и Bot API, и Gemini используют его для исходящих запросов.
- Порядок обработки: `очередь -> Gemini -> (успех) -> Google Sheets`.
- При перезапуске процесса элементы со статусом `processing` возвращаются в `pending` и дообрабатываются.
- На временных ошибках Gemini (429/503/timeout) и Google Sheets включен exponential backoff.
- Количество попыток настраивается в `.env`:
  - `GEMINI_TRANSIENT_RETRIES` (по умолчанию 4)
  - `GEMINI_VALIDATION_RETRIES` (по умолчанию 2)
  - `GOOGLE_APPEND_RETRIES` (по умолчанию 4)
  - `RETRY_BACKOFF_BASE_SECONDS` (по умолчанию 2, значит 2/4/8/16)
  - `PIPELINE_RETRY_MAX_BACKOFF_SECONDS` (потолок задержки между повторными попытками очереди)
- В `GOOGLE_SHEET_URL` можно хранить полную ссылку на таблицу.
- Для безопасности не коммить `.env` и `secrets/`.
