# 04. Архитектура

## Высокоуровневая схема

```
┌──────────────────────────────────────────┐
│       Telegram Mini App (Phaser 3)       │
│  - Игровой канвас (Phaser сцены)         │
│  - HUD overlay (Solid компоненты)        │
│  - Локальный отображаемый стейт          │
│  - Авторизация: WebApp.initData          │
└─────────────────┬────────────────────────┘
                  │ HTTPS REST
                  │ (опц. WS в Beta)
┌─────────────────▼────────────────────────┐
│       FastAPI backend (Python)           │
│  - /api/villager/* endpoints             │
│  - Telegram initData HMAC verify         │
│  - Серверная авторитарность              │
│  - Idle-расчёты при /sync                │
│  - Job runner (asyncio.create_task)      │
└──┬───────────────────────┬───────────────┘
   │                       │
┌──▼─────────┐    ┌────────▼──────────────┐
│ Postgres 16│    │     Redis 7          │
│ источник   │    │   - hot state cache  │
│ истины     │    │   - sorted sets      │
│            │    │     (лидерборды)     │
│ villager_* │    │   - rate limit       │
│ tables     │    │   - pub/sub → bot    │
└────────────┘    └───────┬──────────────┘
                          │
                  ┌───────▼──────────────┐
                  │   aiogram 3 bot      │
                  │ - /villager (WebApp) │
                  │ - /status, /collect  │
                  │ - push notifications │
                  └──────────────────────┘
```

## Принцип: серверная авторитарность

**Клиент не источник правды ни для одного игрового числа.**

Клиент посылает «намерения» (intents), а не результаты:

| Клиент → Сервер | Что значит |
|---|---|
| `POST /api/villager/build` | "Хочу построить лесопилку на (3,5)" |
| `POST /api/villager/upgrade` | "Хочу проапгрейдить здание #42" |
| `POST /api/villager/collect` | "Хочу забрать накопленный idle-доход" |
| `POST /api/villager/quest/claim` | "Хочу забрать награду за квест #7" |

Сервер для каждого:
1. Аутентифицирует пользователя (initData HMAC)
2. Загружает текущее состояние из БД (с FOR UPDATE при необходимости)
3. Проверяет валидность намерения
4. Применяет изменения в транзакции
5. Возвращает обновлённый стейт игроку

**Никогда** клиент не присылает "у меня теперь 100 дерева". Он присылает "я хочу собрать дерево с лесопилки #42", и сервер сам считает сколько накопилось.

## Поток данных: типичный заход в игру

```
1. Игрок открывает Mini App
   └→ Phaser BootScene загружает ассеты
   └→ Параллельно клиент шлёт GET /api/villager/state
       └→ Сервер: проверяет initData HMAC
       └→ Сервер: SELECT из villager_users, villager_buildings, villager_resources
       └→ Сервер: вычисляет idle-доход с last_collected_at до now
       └→ Сервер: возвращает полный snapshot
   └→ Phaser MapScene рендерит карту по snapshot
   └→ UIScene показывает модалку "Собери накопленное"

2. Игрок тапает "Собрать всё"
   └→ POST /api/villager/collect_all
       └→ Сервер: TX BEGIN
       └→ Сервер: для каждого здания — добавить ресурсы (с учётом капа склада)
       └→ Сервер: обновить last_collected_at = now
       └→ Сервер: TX COMMIT
       └→ Возвращает обновлённый стейт
   └→ Клиент анимирует прирост ресурсов

3. Игрок строит новое здание
   └→ Drag-and-drop спрайта на тайл (3, 5)
   └→ POST /api/villager/build {type: "lumbermill", x: 3, y: 5}
       └→ Сервер: проверки (есть ресурсы, тайл свободен, доступна эра)
       └→ Сервер: TX — списать ресурсы, INSERT в villager_buildings, статус='building'
       └→ Сервер: запускает asyncio task с delay = build_time
           └→ когда таска срабатывает: UPDATE status='active', PUBLISH в Redis
               └→ Бот подписан на канал, шлёт push: "🏗️ Лесопилка готова"
       └→ Возвращает обновлённый стейт
   └→ Клиент рисует здание со статусом "стройка" + таймер
```

## Idle-механика (детально)

### Подход: lazy compute (вычисление при доступе)

Не запускаем cron, который каждую секунду наращивает ресурсы у миллиона игроков. Вместо этого:

- В таблице `villager_buildings` храним `last_collected_at`
- Каждое здание знает свою выработку в час: `output_per_hour`
- При запросе `/state` или `/collect` считаем накопление формулой:

```python
delta_hours = (now - last_collected_at).total_seconds() / 3600
# офлайн-эффективность 0.5 если игрок неактивен > 5 минут
efficiency = 0.5 if (now - user.last_seen_at) > 300 else 1.0
# жёсткий кап в 24 часа
delta_hours = min(delta_hours, 24)
produced = int(building.output_per_hour * delta_hours * efficiency)
# не превышаем кап склада
produced = min(produced, storage_cap - current_amount)
```

Преимущество: считаем только когда игрок зашёл. Один игрок — одно вычисление за визит, не миллион/сек cron-job.

### Кап склада
- `villager_resources.cap` — текущий кап для конкретного ресурса
- Растёт при апгрейде Storage и Town Hall
- Когда кап достигнут — здания "стопаются" (не накопляют сверху)

## Стройка (длительные процессы)

Стройка идёт минуты-часы. Реализация:

1. POST /api/villager/build → INSERT строки в `villager_buildings` со статусом `building` + `finish_at = now + build_time`
2. **Без отдельной очереди в MVP**: при запросе `/state` сервер проверяет `WHERE finish_at <= now AND status='building'` → переводит в `active` + триггерит push
3. Параллельно в FastAPI — фоновая asyncio задача: каждые 30 сек сканирует созревшие задачи и шлёт push в Redis pub/sub

В Beta при росте — заменим на BullMQ-подобную очередь.

## Push-нотификации

```
Backend → Redis pub/sub channel "villager:notify"
{
  "tg_id": 123456,
  "type": "building_done",
  "data": {"name": "Лесопилка", "level": 2}
}

aiogram bot подписан на этот канал, формирует сообщение и
шлёт через Telegram Bot API.
```

## Авторизация

```
Каждый запрос Mini App содержит:
  Authorization: tma <Telegram WebApp initData string>

Backend middleware:
  1. Парсит initData (URL-encoded query string)
  2. Извлекает hash, остальные поля
  3. Считает HMAC-SHA256 с секретом BOT_TOKEN
  4. Сравнивает с hash. Если не совпало → 401
  5. Проверяет auth_date свежий (< 24 ч)
  6. Извлекает user.id (telegram_id)
  7. Передаёт telegram_id в handler через Depends(require_user)
```

Это тот же flow что в казино (`app/api/auth.py`).

## Анти-чит (минимум для MVP)

| Защита | Реализация |
|---|---|
| Подмена пользователя | initData HMAC (стандарт Telegram) |
| Спам запросов | Redis rate limit (60 RPS на user, 10 RPS на endpoint) |
| Невозможные значения | Валидация Pydantic + проверка ресурсов в БД с FOR UPDATE |
| Десинк клиент/сервер | Все игровые числа на сервере, клиент только отображает |
| Дублирующиеся транзакции | Идемпотентные ключи на стороне build/upgrade/collect |
| Replay атаки | auth_date проверка, nonce в чувствительных запросах |

ML-детекция, behavioral analysis — не в MVP.

## Версионирование

- В заголовках API: `X-Game-Version: 1.0.0`
- При несовпадении мажорной версии клиент показывает "обновись"
- Мажорные изменения схемы БД — через миграции `app/villager/audit.py` (по аналогии с казино)

## Конфигурация

Балансные числа (стоимости, выработка, время стройки) — в JSON-файлах:

```
app/villager/config/
├── buildings.json          — все здания, ур., цены
├── resources.json          — все ресурсы, базовые капы
├── quests.json             — определения квестов
└── eras.json               — требования эпох
```

Это **критично**: чтобы менять баланс — не нужно редеплоиться. Просто правим JSON, restart pod (минуту простоя на ПК).

## Локальные конфиги клиента

Клиент при загрузке тянет `GET /api/villager/config` и кеширует. Нужно для:
- Иконок (имя файла)
- Локализации названий
- Базовой инфы (тултипы, описания)

Никогда не используется для реальных решений — только для отображения.
