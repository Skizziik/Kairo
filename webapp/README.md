# RIP Casino Mini App

Статический фронтенд Telegram Mini App для казино/кейсов Kairo бота.

## Как это хостится

Render Static Site, автоматически деплоится при push в `main` если меняется что-то в `webapp/`.

После первого деплоя:
1. Скопируй URL из Render (например `https://kairo-casino-app.onrender.com`)
2. Вставь его в env `MINIAPP_URL` у основного сервиса `kairo`
3. Сервис перезапустится, и команда `/casino` в боте начнёт показывать кнопку "Открыть казино"

## Локальная разработка

```bash
cd webapp
python -m http.server 8080
# open http://localhost:8080
```

**Но:** без Telegram WebApp контекста auth не проходит. Для полной отладки нужно деплоить и открывать через бота.

## Конфиг

API эндпоинт читается из `window.KAIRO_API_BASE` — можно переопределить через `config.js` или inline script, по умолчанию `https://kairo-em51.onrender.com` (твой Render backend URL).

## Структура

- `index.html` — разметка, 5 views (home/cases/case-preview/case-open/inventory/leaderboard)
- `styles.css` — тёмная тема, цвета раритетов как в CS2
- `app.js` — весь клиентский код (vanilla JS, no framework)
