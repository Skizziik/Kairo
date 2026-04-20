# Kairo — RIP нагибатор

Telegram-бот для группы **RIP CS2**. Даёт тактику и токсик-ответы через Cerebras,
собирает 5-стак, тянет Steam статы, скачивает треки с YouTube, помнит кто есть кто
(pgvector + Supabase).

Стек: Python 3.11, aiogram 3, FastAPI (webhook), Cerebras (qwen3-235b-instruct),
Supabase Postgres + pgvector, fastembed (multilingual-e5-small).
Хостинг: Render free (Docker) + UptimeRobot пинг каждые 5 мин.

## Архитектура

```
Telegram ──webhook──► FastAPI (/webhook/<secret>)
                         │
                         ├─► aiogram Dispatcher ─► handlers/*
                         │         │
                         │         ├─ middlewares: antispam, message-log
                         │         └─ AI: Cerebras + fastembed + pgvector memory
                         │
                         └─► /health ◄── UptimeRobot keep-alive
```

Каждое сообщение в группе пишется в `messages`. Раз в `MEMORY_EXTRACT_EVERY`
сообщений запускается фоновый экстрактор: LLM читает окно и обновляет
`user_profiles` + эмбеддит свежие факты в `memories`. Когда кто-то спрашивает
`/ai`, промпт собирается из: персоны, профиля автора, top-K семантических
воспоминаний и последних N сообщений чата.

## Файлы

```
main.py                      FastAPI entry + webhook
app/
  config.py                  env settings (pydantic)
  bot.py                     aiogram Bot + Dispatcher + routers
  db/
    schema.sql               запустить руками в Supabase SQL Editor
    client.py                asyncpg pool
    repos.py                 user/message/memory repositories
  ai/
    cerebras.py              OpenAI-compatible client с retry
    embeddings.py            fastembed wrapper (lazy load)
    prompts.py               персона + экстрактор
    memory.py                retrieval + extraction
  handlers/
    start, help, whereami
    ai_chat, tldr
    lfg (5-стак с инлайн-кнопками)
    stats, inv (Steam)
    map, yt, me, top
  middlewares/
    logging.py               upsert user + log message + trigger extractor
    antispam.py               rate limit per user
  services/
    steam.py                 Steam Web API
    youtube.py               yt-dlp в mp3
```

## Деплой (Render + Supabase + UptimeRobot)

### 1. Supabase

1. [supabase.com](https://supabase.com/) → New Project (free tier, region ближе к Frankfurt).
2. Database → **Connection string** → выбери **Session pooler** (порт 5432) →
   скопируй URI, подставь свой пароль. Это `DATABASE_URL`.
3. SQL Editor → вставь и запусти содержимое `app/db/schema.sql`.

### 2. Render

1. [render.yaml](render.yaml) уже лежит в репо — Render его подхватит.
2. New → Blueprint → выбери форк/репо `Skizziik/Kairo` → Apply.
3. Когда сервис создан, в Dashboard → Environment заполни секреты (они помечены
   `sync: false` в render.yaml — Render попросит ввести вручную):
   - `TG_BOT_TOKEN` — от @BotFather
   - `TG_WEBHOOK_SECRET` — придумай рандомную строку 32+ символов
   - `TG_ALLOWED_CHAT_ID` — пусто пока (заполним после `/whereami`)
   - `TG_ADMIN_IDS` — твой tg_id (можно пусто пока)
   - `CEREBRAS_API_KEY` — с [cloud.cerebras.ai](https://cloud.cerebras.ai/)
   - `DATABASE_URL` — из Supabase
   - `STEAM_API_KEY` — с [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey)
   - `PUBLIC_BASE_URL` — URL сервиса который выдал Render (например
     `https://kairo.onrender.com`) — без слэша в конце.
4. Deploy. Первая сборка минут 5–8 (фаза `RUN python -c fastembed` качает ONNX
   модель в образ).
5. Проверь: `https://<your>.onrender.com/health` → должно вернуть `ok`.

### 3. Telegram

1. Добавь бота в группу «RIP CS2» как админа (права: Delete messages, Ban users,
   Pin, Invite via link, Manage video chats).
2. В группе напиши `/whereami` → бот ответит `chat_id` и твой `tg_id`.
3. Скопируй эти значения в Render → Environment → `TG_ALLOWED_CHAT_ID` и
   `TG_ADMIN_IDS` → жми Save. Render перезапустит сервис.
4. Проверь `/start`, `/help`, `/map`, `/ai привет`.

### 4. UptimeRobot (чтобы Render не засыпал)

1. [uptimerobot.com](https://uptimerobot.com) → Add New Monitor.
2. Type: **HTTP(s)**, URL: `https://<your>.onrender.com/health`,
   Interval: **5 minutes**, Monitor Type — GET.
3. Save. Render free tier usage ~ 720 ч/мес (всё норм в пределах 750).

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env  # заполни значения
python -m uvicorn main:app --reload --port 10000
```

Для локала понадобится публичный URL (ngrok/cloudflared) чтобы Telegram мог
достучаться webhook'ом. Или переключи на polling (не коммичу в мастер, делай
отдельно).

## Важные нюансы

- **Privacy mode у бота должен быть Disabled** в @BotFather → `/setprivacy` →
  Disable. Иначе бот не увидит сообщения в группе без `/` или реплая, и память
  не будет работать.
- **fastembed модель ~130 MB в RAM** + python + aiogram ≈ 350–470 MB из 512 MB
  Render free. Если OOM — замени `intfloat/multilingual-e5-small` на
  `BAAI/bge-small-en-v1.5` (меньше, но хуже русский) или вынеси эмбеддинги в
  Supabase Edge Function.
- **Холодный старт** после сна Render ~30 сек. UptimeRobot держит живым, но
  первый деплой / перезапуск даст этот лаг.
- **Secrets никогда в git.** Всё через Render env или `.env` локально
  (`.env` в `.gitignore`).
