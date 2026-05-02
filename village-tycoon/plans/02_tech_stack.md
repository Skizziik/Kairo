# 02. Технологический стек

## Frontend (игровой клиент)

### Рендер: Phaser 3
- **Версия**: 3.80+
- **Почему**: зрелый 2D-движок для веба, тайлмапы и спрайты из коробки, отличные доки, LLM хорошо знает API, активное community
- **Что делает**: рендер карты, спрайтов зданий, анимации, particles, drag-and-drop, pinch-zoom камера, input
- **Альтернативы рассмотрены**: Pixi.js (слишком низкоуровневый для tycoon), Cocos (тяжёлый, плохо для AI-разработки), Defold (Lua only)

### Язык: TypeScript
- 200+ зданий, 42 ресурса, 100+ квестов — без типизации это ад
- Phaser 3 имеет официальные TS-типы

### Билдер: Vite
- Быстрый dev-сервер с HMR
- Простая конфигурация
- Хорошо работает с TS и Phaser

### UI overlay
- Канвас Phaser отвечает за карту и игровые объекты
- HUD, меню, модалки, тултипы — поверх через DOM
- **Решение**: Solid.js — лёгкий (~10KB), реактивный, без виртуального DOM, проще чем React для overlay-задач
- Альтернатива: vanilla TS + Web Components (если Solid избыточен)

### State management
- **Игровой state на сервере** (источник истины), клиент — отображение
- На клиенте — Zustand-подобный лёгкий стор, обновляется при /sync ответах сервера

### Структура проекта
```
village-tycoon/
├── src/
│   ├── main.ts                    — entry, инициализация Phaser
│   ├── scenes/                    — игровые сцены Phaser
│   │   ├── BootScene.ts           — загрузка ассетов
│   │   ├── MapScene.ts            — главная карта
│   │   └── UIScene.ts             — HUD overlay
│   ├── systems/                   — игровые системы
│   │   ├── BuildingSystem.ts
│   │   ├── ResourceSystem.ts
│   │   └── IdleSystem.ts
│   ├── api/                       — клиент API
│   ├── ui/                        — Solid компоненты
│   ├── data/                      — локальные конфиги (зеркало с сервера)
│   └── types/                     — общие типы
├── public/
│   └── assets/                    — PNG, JSON атласы
├── index.html
├── vite.config.ts
├── tsconfig.json
└── package.json
```

## Backend

### Базовый стек: продолжаем Python
- **FastAPI** — REST API
- **asyncpg** — Postgres драйвер (быстрее чем SQLAlchemy для нашего случая)
- **Pydantic v2** — валидация моделей
- **uvicorn** — ASGI сервер

### БД: Postgres 16
- Реляционная для всей точной экономики
- Отдельные таблицы с префиксом `villager_` (чтобы не путать с казино-таблицами)
- Если решим разделить — позже создадим отдельную БД

### Кеш / очереди: Redis 7
- Hot data (текущий стейт игрока 1ч TTL)
- Sorted sets для лидербордов
- Pub/sub для уведомлений между бэкендом и ботом
- Для долгих процессов (стройка) — пока без BullMQ-подобных систем, обходимся `asyncio.create_task` + БД-таблица `villager_jobs`

### Реалтайм: WebSocket (опционально)
- В MVP **не нужен**
- Появится в Beta для чата гильдии и live-биржи
- FastAPI поддерживает WS нативно через `websockets`

## Bot

### aiogram 3 (общий с казино)
- Один бот, один процесс — два хендлера: `/casino` и `/villager`
- Кнопки WebApp ведут на свои URL
- Push-нотификации игроку через бота: "постройка готова", "рейд готов" и т.д.

### Команды Villager
- `/villager` — открывает Mini App с WebAppInfo
- `/status` — быстрый ответ боту: уровень, ресурсы, незавершённые процессы
- `/collect` — собрать офлайн-доход без открытия игры
- `/help` — гайд

## Infrastructure

### Локальный dev — Docker Compose
```yaml
# Подробности в docker-compose.yml
services:
  postgres:    # Postgres 16
  redis:       # Redis 7
  backend:     # FastAPI app
  frontend:    # Vite dev server
  bot:         # aiogram polling (или webhook через tunnel)
  cloudflared: # Cloudflare Tunnel для публичного HTTPS
```

### Cloudflare Tunnel
- Бесплатно
- Стабильный subdomain (нужно купить домен ~$10/год или получить бесплатный *.trycloudflare.com)
- Туннелит локальный 5173 на публичный HTTPS

### Production hosting (когда придёт время)
- VPS Hetzner CX22 ($5-10/мес) — Docker Compose
- Backups через rsync на внешний диск + еженедельный pg_dump
- Sentry для error tracking (free tier)

## Auth

- **Telegram WebApp initData** через HMAC-верификацию (как у казино)
- Каждый запрос содержит `Authorization: tma <initData>`
- Бэкенд проверяет подпись, извлекает `user.id` (telegram_id)
- Тот же flow что в существующей казино-апке — переиспользуем код

## Платежи

- В MVP **не нужны**
- В Beta — Telegram Stars через бота (нативный API)
- На стороне бэкенда — webhook от Telegram + идемпотентные транзакции

## Логи и мониторинг

- **Sentry** для ошибок (free tier до 5к событий/мес)
- **Логи в Postgres** (`villager_event_log`) для аналитики геймплея
- **Grafana + Prometheus** — позже, когда будет реальная нагрузка

## Решённые альтернативы

| Что рассматривали | Почему НЕ |
|---|---|
| Node.js + Fastify (как в GDD) | У нас уже работает Python-стек, дублировать смысла нет |
| Cocos Creator | Тяжёлый рантайм, плохой DX для AI-flow |
| Unity → WebGL | Огромные билды, плохо для Telegram |
| Three.js | 3D overkill для 2D iso |
| MongoDB | Реляционные данные с транзакциями — не подходит |
| Static React app + serverless | Серверная авторитарность критична для анти-чита, serverless с Postgres = боль |
