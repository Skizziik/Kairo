# 05. Схема БД

Все таблицы префиксованы `villager_` чтобы не путать с казино-таблицами.

Postgres 16 + расширения: `pgcrypto` (для UUID), `pg_trgm` (для поиска по никнеймам в Beta).

## Главные таблицы MVP

### villager_users — игроки

```sql
CREATE TABLE villager_users (
  tg_id            BIGINT PRIMARY KEY,
  username         TEXT,
  first_name       TEXT,
  last_name        TEXT,
  language_code    TEXT DEFAULT 'ru',
  is_premium       BOOLEAN DEFAULT false,

  -- игровая прокачка
  village_name     TEXT NOT NULL DEFAULT 'Моя деревня',
  era              SMALLINT NOT NULL DEFAULT 1,
  player_level     INTEGER NOT NULL DEFAULT 1,
  experience       BIGINT NOT NULL DEFAULT 0,

  -- премиум
  gems_balance     BIGINT NOT NULL DEFAULT 0,
  pass_active_until TIMESTAMPTZ,

  -- модерация
  banned           BOOLEAN NOT NULL DEFAULT false,
  ban_reason       TEXT,

  -- мета
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_sync_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_villager_users_last_seen
  ON villager_users(last_seen_at) WHERE banned = false;
```

### villager_buildings — постройки игрока

```sql
CREATE TABLE villager_buildings (
  id                  BIGSERIAL PRIMARY KEY,
  tg_id               BIGINT NOT NULL REFERENCES villager_users(tg_id),
  building_type       TEXT NOT NULL,            -- 'townhall', 'lumbermill', etc.
  level               SMALLINT NOT NULL DEFAULT 1,

  -- размещение на карте
  position_x          SMALLINT NOT NULL,
  position_y          SMALLINT NOT NULL,

  -- статус
  status              TEXT NOT NULL DEFAULT 'active',  -- active | building | upgrading | demolishing
  finish_at           TIMESTAMPTZ,                       -- когда статус закончится

  -- idle-производство
  last_collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- метаданные
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- ограничения целостности
  UNIQUE (tg_id, position_x, position_y)
);

CREATE INDEX idx_villager_buildings_user ON villager_buildings(tg_id);
CREATE INDEX idx_villager_buildings_finish
  ON villager_buildings(finish_at)
  WHERE status IN ('building', 'upgrading');
```

### villager_resources — ресурсы игрока

```sql
CREATE TABLE villager_resources (
  tg_id           BIGINT NOT NULL REFERENCES villager_users(tg_id),
  resource_type   TEXT NOT NULL,         -- 'wood', 'stone', 'food', etc.
  amount          NUMERIC NOT NULL DEFAULT 0,
  cap             NUMERIC NOT NULL DEFAULT 1000,
  PRIMARY KEY (tg_id, resource_type)
);
```

Используем `NUMERIC` (без precision limit) — на случай если кто-то насобирает огромные числа (как было с казино у Игоря).

### villager_quests_progress — прогресс квестов

```sql
CREATE TABLE villager_quests_progress (
  tg_id           BIGINT NOT NULL REFERENCES villager_users(tg_id),
  quest_id        TEXT NOT NULL,         -- 'q_first_lumbermill'
  status          TEXT NOT NULL DEFAULT 'active',  -- active | completed | claimed
  progress        JSONB NOT NULL DEFAULT '{}',
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  claimed_at      TIMESTAMPTZ,
  PRIMARY KEY (tg_id, quest_id)
);

CREATE INDEX idx_villager_quests_active
  ON villager_quests_progress(tg_id) WHERE status = 'active';
```

### villager_event_log — лог действий

```sql
CREATE TABLE villager_event_log (
  id          BIGSERIAL PRIMARY KEY,
  tg_id       BIGINT NOT NULL,
  event_type  TEXT NOT NULL,        -- 'building_built', 'resources_collected', 'quest_done'
  data        JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_villager_event_log_user_time
  ON villager_event_log(tg_id, created_at DESC);

-- Партиции по месяцам (когда таблица превысит ~10М строк)
-- ALTER TABLE villager_event_log PARTITION BY RANGE (created_at);
```

## Таблицы для Beta (создаются позже)

### villager_citizens — жители (когда появятся как сущности)

```sql
CREATE TABLE villager_citizens (
  id              BIGSERIAL PRIMARY KEY,
  tg_id           BIGINT NOT NULL REFERENCES villager_users(tg_id),
  name            TEXT NOT NULL,
  age             SMALLINT NOT NULL DEFAULT 18,
  profession      TEXT,                    -- 'lumberjack', 'farmer', etc.
  skill_level     SMALLINT NOT NULL DEFAULT 1,
  trait           TEXT,
  loyalty         SMALLINT NOT NULL DEFAULT 100,
  happiness       SMALLINT NOT NULL DEFAULT 50,
  building_id     BIGINT REFERENCES villager_buildings(id),
  born_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### villager_inventory — предметы (Beta+)

```sql
CREATE TABLE villager_inventory (
  id          BIGSERIAL PRIMARY KEY,
  tg_id       BIGINT NOT NULL REFERENCES villager_users(tg_id),
  item_type   TEXT NOT NULL,
  rarity      TEXT,
  quantity    INTEGER NOT NULL DEFAULT 1,
  metadata    JSONB DEFAULT '{}',           -- enchantments, durability
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_villager_inventory_user ON villager_inventory(tg_id);
```

### villager_technologies — открытые технологии (Beta+)

```sql
CREATE TABLE villager_technologies (
  tg_id           BIGINT NOT NULL REFERENCES villager_users(tg_id),
  tech_id         TEXT NOT NULL,
  unlocked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tg_id, tech_id)
);
```

### villager_guilds — гильдии (Beta+)

```sql
CREATE TABLE villager_guilds (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT UNIQUE NOT NULL,
  description     TEXT,
  emblem          TEXT,
  level           SMALLINT NOT NULL DEFAULT 1,
  member_count    SMALLINT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE villager_guild_members (
  guild_id    BIGINT NOT NULL REFERENCES villager_guilds(id),
  tg_id       BIGINT NOT NULL REFERENCES villager_users(tg_id) UNIQUE,
  role        TEXT NOT NULL DEFAULT 'member', -- leader | officer | member
  joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (guild_id, tg_id)
);
```

### villager_auction_listings — аукцион (Beta+)

```sql
CREATE TABLE villager_auction_listings (
  id                  BIGSERIAL PRIMARY KEY,
  seller_tg_id        BIGINT NOT NULL,
  item_type           TEXT NOT NULL,
  quantity            INTEGER NOT NULL,
  starting_price      INTEGER NOT NULL,
  current_price       INTEGER NOT NULL,
  buy_now_price       INTEGER,
  current_bidder_id   BIGINT,
  expires_at          TIMESTAMPTZ NOT NULL,
  status              TEXT NOT NULL DEFAULT 'active',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_villager_auction_active
  ON villager_auction_listings(expires_at) WHERE status = 'active';
```

## Миграционный модуль

`app/villager/audit.py` — функция `ensure_schema(conn)`, вызывается при старте FastAPI. Идемпотентна: проверяет наличие таблиц, создаёт недостающие, мигрирует если изменилась схема. По аналогии с `app/economy/audit.py`.

```python
async def ensure_schema(conn):
    # 1. Создать таблицы если их нет (CREATE TABLE IF NOT EXISTS)
    # 2. Добавить столбцы если их не было (ALTER TABLE ... ADD COLUMN IF NOT EXISTS)
    # 3. Мигрировать типы если поменялись (DO ... block с проверкой)
    # 4. Создать индексы (CREATE INDEX IF NOT EXISTS)
    # 5. Сделать UPSERT в villager_users для существующего юзера если нужно
    pass
```

## Redis ключи

| Ключ | Тип | TTL | Назначение |
|---|---|---|---|
| `villager:state:{tg_id}` | Hash | 5 мин | Кеш стейта игрока |
| `villager:rate:{tg_id}:{endpoint}` | INCR | 1 мин | Rate limit |
| `villager:notify` | pub/sub | — | Канал нотификаций бот↔backend |
| `villager:lb:wealth` | Sorted Set | — | Лидерборд богатства (Beta) |
| `villager:lb:level` | Sorted Set | — | Лидерборд уровня (Beta) |
| `villager:online:{tg_id}` | String | 5 мин | Признак онлайна (для эффективности) |

## Стратегия резервного копирования

- pg_dump раз в день, хранение 30 дней
- Бэкап на внешний диск + по желанию в S3 (b2.backblaze.com — дёшево)
- Тест восстановления раз в месяц

## Капы и масштаб

| Таблица | Ожидаемый размер MVP | Beta | Production |
|---|---|---|---|
| villager_users | ~50 | ~5k | ~500k |
| villager_buildings | ~500 | ~50k | ~10М |
| villager_resources | ~300 | ~30k | ~6М |
| villager_event_log | ~50k | ~5М | ~10B (партиции по дате) |

В MVP — даже SQLite справился бы. Берём Postgres сразу чтобы не переезжать.
