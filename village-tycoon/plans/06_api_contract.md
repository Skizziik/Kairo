# 06. API Contract

REST API для Phaser-клиента. Все endpoints под префиксом `/api/villager/`.

## Авторизация

Каждый запрос содержит:
```
Authorization: tma <Telegram WebApp initData string>
```

При неверном/просроченном initData — `401 Unauthorized`.

## Общий формат ответов

Успех:
```json
{
  "ok": true,
  "data": { ... }
}
```

Ошибка:
```json
{
  "ok": false,
  "error": "not_enough_resources",
  "message": "Не хватает 50 дерева"
}
```

Коды ошибок:

| Код | Когда |
|---|---|
| `not_authenticated` | initData невалиден |
| `banned` | Игрок забанен |
| `rate_limit` | Слишком много запросов |
| `not_enough_resources` | Не хватает ресурсов |
| `invalid_position` | Тайл вне поля или занят |
| `not_found` | Здание/квест не найдены |
| `era_locked` | Не доступно в текущей эпохе |
| `cap_reached` | Достигнут лимит склада |
| `concurrent_modification` | Race condition, повтори запрос |

---

## Endpoints

### `GET /api/villager/config`

Возвращает справочную информацию (читается клиентом 1 раз при загрузке + при смене версии). Все балансные числа.

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "version": "1.0.0",
    "buildings": {
      "lumbermill": {
        "name": "Лесопилка",
        "size": [2, 2],
        "max_level": 3,
        "icon": "building_lumbermill_lvl1.png",
        "levels": [
          {
            "level": 1,
            "cost": {"wood": 50, "stone": 0},
            "build_time_seconds": 30,
            "output_per_hour": {"wood": 100},
            "icon": "building_lumbermill_lvl1.png"
          },
          { "level": 2, "cost": {...}, ... }
        ],
        "era": 1,
        "description": "Производит дерево из окружающего леса."
      },
      ...
    },
    "resources": {
      "wood": {"name": "Дерево", "icon": "res_wood.png"},
      ...
    },
    "tile_size": 128,
    "map_size": [16, 16]
  }
}
```

---

### `GET /api/villager/state`

Полный snapshot текущего стейта игрока. Вызывается при открытии Mini App.

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "user": {
      "tg_id": 12345,
      "village_name": "Моя деревня",
      "era": 1,
      "player_level": 4,
      "experience": 1250,
      "experience_to_next": 2000,
      "gems_balance": 50
    },
    "resources": [
      {"type": "wood", "amount": "1234", "cap": "5000"},
      {"type": "stone", "amount": "456", "cap": "5000"},
      {"type": "food", "amount": "789", "cap": "3000"},
      {"type": "water", "amount": "100", "cap": "2000"},
      {"type": "gold", "amount": "200", "cap": "10000"}
    ],
    "buildings": [
      {
        "id": 42,
        "type": "townhall",
        "level": 2,
        "x": 7,
        "y": 7,
        "status": "active",
        "finish_at": null,
        "pending_collect": {"gold": 5}
      },
      {
        "id": 43,
        "type": "lumbermill",
        "level": 1,
        "x": 5,
        "y": 8,
        "status": "building",
        "finish_at": "2026-05-02T15:30:00Z",
        "pending_collect": {}
      }
    ],
    "quests": [
      {
        "id": "q_first_lumbermill",
        "name": "Построй лесопилку",
        "description": "...",
        "status": "completed",
        "rewards": {"gold": 50, "experience": 100}
      }
    ],
    "offline_summary": {
      "total_seconds": 28800,
      "resources_gained": {"wood": 800, "stone": 0, "food": 200},
      "buildings_finished": 1
    },
    "server_time": "2026-05-02T12:00:00Z"
  }
}
```

`offline_summary` — это то, что показывается в модалке "Пока тебя не было".

`pending_collect` — сколько накоплено в этом конкретном здании (если игрок предпочитает собирать поштучно).

---

### `POST /api/villager/build`

Построить новое здание.

**Request**:
```json
{
  "type": "lumbermill",
  "x": 5,
  "y": 8
}
```

**Response 200** (успех):
```json
{
  "ok": true,
  "data": {
    "building": {
      "id": 43,
      "type": "lumbermill",
      "level": 1,
      "x": 5,
      "y": 8,
      "status": "building",
      "finish_at": "2026-05-02T12:00:30Z"
    },
    "resources": [...]   // обновлённые ресурсы после списания
  }
}
```

**Response 400**:
- `not_enough_resources`
- `invalid_position` (вне карты, на воде, на дороге)
- `position_occupied`
- `era_locked` (тип не открыт в текущей эпохе)
- `building_limit_reached` (лимит количества данного типа достигнут)

---

### `POST /api/villager/upgrade`

Проапгрейдить существующее здание.

**Request**:
```json
{
  "building_id": 43
}
```

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "building": {
      "id": 43,
      "type": "lumbermill",
      "level": 1,                           // ещё не увеличился, апгрейд в процессе
      "status": "upgrading",
      "finish_at": "2026-05-02T12:05:00Z",
      "next_level": 2
    },
    "resources": [...]
  }
}
```

---

### `POST /api/villager/collect`

Забрать накопленные ресурсы из конкретного здания.

**Request**:
```json
{
  "building_id": 43
}
```

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "collected": {"wood": 234},
    "resources": [...]
  }
}
```

---

### `POST /api/villager/collect_all`

Забрать накопленные ресурсы со всех зданий разом.

**Request**: пустое тело.

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "collected": {"wood": 1234, "stone": 567, "food": 89},
    "buildings_collected": 5,
    "resources": [...]
  }
}
```

---

### `POST /api/villager/move`

Переместить здание на новую позицию (бесплатно в MVP).

**Request**:
```json
{
  "building_id": 43,
  "x": 6,
  "y": 8
}
```

---

### `POST /api/villager/demolish`

Снести здание. Возвращает 50% стоимости в ресурсах.

**Request**:
```json
{
  "building_id": 43
}
```

---

### `POST /api/villager/quest/claim`

Забрать награду за завершённый квест.

**Request**:
```json
{
  "quest_id": "q_first_lumbermill"
}
```

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "rewards_received": {"gold": 50, "experience": 100, "gems": 5},
    "resources": [...],
    "user": {...},          // обновлённый user (level/exp могли измениться)
    "next_quests": [...]    // новые квесты, разблокированные этим
  }
}
```

---

### `POST /api/villager/speedup`

Ускорить процесс (стройку или апгрейд) за гемы.

**Request**:
```json
{
  "building_id": 43
}
```

**Response 200**:
```json
{
  "ok": true,
  "data": {
    "building": {... status: 'active' ...},
    "gems_spent": 5,
    "user": {...}
  }
}
```

---

### `GET /api/villager/leaderboard?type=level&limit=100`

(Beta) Лидерборд.

---

## WebSocket (Beta)

```
ws://.../api/villager/ws
```

События сервер → клиент:
- `building_finished` — постройка завершилась
- `quest_unlocked` — открылся новый квест
- `notification` — общее уведомление
- `pulse` — состояние "ты онлайн" (раз в 30 сек)

В MVP без WebSocket. Polling раз в 30 секунд через `/state` если приложение в foreground.

---

## Rate limits

| Endpoint | Лимит |
|---|---|
| `/state` | 30 req/min |
| `/config` | 5 req/min |
| `/build`, `/upgrade`, `/move`, `/demolish` | 30 req/min |
| `/collect`, `/collect_all` | 60 req/min |
| `/quest/claim` | 30 req/min |
| `/speedup` | 30 req/min |

При превышении — `429 Too Many Requests` с заголовком `Retry-After`.

## Идемпотентность

Чувствительные операции (build, upgrade, collect, claim) принимают опциональный заголовок `X-Idempotency-Key: <uuid>`. Если такой ключ уже обрабатывался за последние 24 ч — возвращается тот же ответ что был, без двойного эффекта. Это защита от двойных кликов и потерянных HTTP-ответов.
