# Village Tycoon

Telegram Mini App в стиле Clash of Clans / Hay Day. Idle / city-builder / tycoon.

Отдельная игра от казино (TGcs2). Делим один репозиторий и общую инфраструктуру (бот, БД), но игра самостоятельная — открывается командой `/villager`.

## Статус

**Этап: Pre-production / planning.** Код пока не пишем, формируем план.

## Стек (утверждено)

- **Frontend**: Phaser 3 + TypeScript + Vite, React-overlay для HUD/меню
- **Backend**: FastAPI + asyncpg + Postgres + Redis (тот же стек что у казино)
- **Bot**: aiogram 3 (общий с казино), новый хендлер `/villager`
- **Hosting MVP**: Docker Compose локально на ПК + Cloudflare Tunnel для публичного HTTPS
- **Hosting prod**: TBD (вероятно VPS Hetzner / DigitalOcean)
- **Auth**: Telegram WebApp initData (как в казино)

## Карта файлов планирования

```
village-tycoon/
├── README.md                       — этот файл
└── plans/
    ├── 01_audit.md                 — аудит GDD, что реально/нереально
    ├── 02_tech_stack.md            — обоснование стека
    ├── 03_mvp_scope.md             — границы MVP
    ├── 04_architecture.md          — клиент/сервер схема
    ├── 05_db_schema.md             — DDL для villager_*
    ├── 06_api_contract.md          — REST endpoints
    ├── 07_asset_pipeline.md        — спека ассетов + ПРОМПТЫ ДЛЯ ГЕНЕРАЦИИ
    ├── 08_visual_language.md       — палитра, типографика, UI
    ├── 10_roadmap.md               — этапы разработки
    └── 11_risks.md                 — риски и митигация
```

## Roadmap кратко

| Этап | Цель | Срок |
|------|------|------|
| **0. Planning** | План-файлы, спеки, Docker-стек | 1-2 дня |
| **1. Foundation** | БД, API-каркас, Phaser-сцена с пустой картой, auth | 3-5 дней |
| **2. MVP** | 5-8 зданий, idle-доход, склад, базовые квесты | 2-3 недели |
| **3. Beta** | Эпохи 3-4, гильдии (минимум), аукцион | 4-6 недель |
| **4. Production** | Полный GDD контент, рейды, эпохи 5-8 | месяцы |

## Ключевая директория ассетов

`village-tycoon/public/assets/` — сюда складываются все PNG, сгенерированные пользователем по спеке из [07_asset_pipeline.md](plans/07_asset_pipeline.md).

## Запуск

Бэкенд уже работает на Render (тот же сервис что у казино — он получил новые `/api/villager/*` endpoints + бот-команду `/villager`). Локально мы запускаем **только фронт** + туннель.

### Один раз — настройка Cloudflare Tunnel

1. Зайди на https://one.dash.cloudflare.com → Networks → Tunnels → Create tunnel
2. Назови `village-tycoon`, выбери "Cloudflared"
3. Скопируй **token** (длинная строка) — пропиши в `village-tycoon/.env`:
   ```
   TUNNEL_TOKEN=eyJh...ваш_токен
   ```
4. В разделе Public Hostname добавь правило:
   - Subdomain: `villager` (или любое)
   - Domain: твой Cloudflare-домен (или бесплатный `*.trycloudflare.com`)
   - Service: `http://vt-frontend:5173`
5. Запомни итоговый URL, например `https://villager.example.com`

### На Render (бэкенд)

В Environment настройках Render-сервиса добавь:
```
VILLAGER_URL=https://villager.example.com
```
И задеплой (Manual Deploy).

### Локально на ПК

```bash
cd village-tycoon
cp .env.example .env       # вставь TUNNEL_TOKEN
docker compose up
```

Vite поднимется на `:5173`, туннель пробросит на твой публичный домен. Открывай `/villager` в Telegram → жми кнопку → должна открыться твоя деревня.

### Дев в браузере (без Telegram)

Можно тестить в обычном браузере, авторизация будет проваливаться, но Phaser-сцена должна загрузиться:
```bash
docker compose up frontend
# → http://localhost:5173
```
