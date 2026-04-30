-- Migration: Биржа TRYLLA. Idempotent. 8 tables for the trading mini-game.
-- Currency: TRYLLA = internal market currency. 1 TRYLLA = 1 main coin (1:1
-- conversion goes through accrue_tax). Starting capital: 100K TRYLLA.

-- ============================================================
-- 1. PER-PLAYER STATE
-- ============================================================
create table if not exists market_users (
    tg_id              bigint primary key references users(tg_id) on delete cascade,
    -- Cash balance in TRYLLA (×1 = whole units, no fractions)
    -- Starting capital: 5,000 TRYLLA = 500,000 cents (×100 precision).
    -- Игрок начинает как «начинающий трейдер с маленькими ставками»:
    -- может купить дробями ЛЮБЫЕ активы (точность 0.000001) и постепенно
    -- наращивать позицию. Mythic-активы за миллионы — endgame-цель.
    trylla             bigint not null default 500000,    -- 5K TRYLLA in cents
    level              int    not null default 1,
    xp                 bigint not null default 0,
    -- Skill levels: {skill_key: int}
    skills             jsonb  not null default '{}'::jsonb,

    -- Lifetime stats
    total_trades       int    not null default 0,
    total_invested     bigint not null default 0,
    total_realized_pl  bigint not null default 0,
    best_trade_pl      bigint not null default 0,
    worst_trade_pl     bigint not null default 0,
    win_count          int    not null default 0,
    loss_count         int    not null default 0,

    -- Privacy: who can view this player's portfolio?
    -- 'public' (anyone), 'friends' (friends only), 'private' (only paid subs)
    portfolio_privacy  text   not null default 'public',

    -- Subscriber count + earnings from selling subs
    subscriber_count   int    not null default 0,

    last_active_at     timestamptz,
    created_at         timestamptz not null default now()
);
create index if not exists idx_market_users_xp on market_users (xp desc);
create index if not exists idx_market_users_pl on market_users (total_realized_pl desc);


-- ============================================================
-- 2. ASSET CATALOG
-- ============================================================
create table if not exists market_assets (
    key            text primary key,
    category       text not null,                  -- crypto/metals/energy/stocks/tech/rare/agro/indexes
    subcategory    text,                           -- mainstream/altcoin/meme/precious/strategic/...
    name           text not null,
    symbol         text not null,
    rarity         text not null default 'common', -- common/uncommon/rare/epic/legendary/mythic
    base_price     bigint not null,                -- starting / "fair" anchor price (in TRYLLA cents — ×100 for precision)
    current_price  bigint not null,                -- live price (×100)
    volatility     real   not null default 0.5,    -- 0..1, drives random-walk magnitude
    liquidity      real   not null default 1.0,    -- 0..1, 1.0 = instant fill, 0.3 = thin
    -- Sector / theme tags for news routing (e.g. ["ai","tech","semiconductor"])
    tags           jsonb  not null default '[]'::jsonb,
    -- Cyclic component params (each asset has its own seasonal tide)
    cycle_period_sec int  not null default 3600,
    cycle_amplitude  real not null default 0.0,    -- 0..0.05 typical
    cycle_phase      real not null default 0.0,    -- 0..2π
    -- Last 24h stats (rolling, updated each price tick)
    high_24h       bigint not null default 0,
    low_24h        bigint not null default 0,
    open_24h       bigint not null default 0,
    volume_24h     bigint not null default 0,
    description    text,
    image_path     text,                           -- relative to webapp/img/market/
    last_tick_at   timestamptz,
    created_at     timestamptz not null default now()
);
create index if not exists idx_market_assets_category on market_assets (category);
create index if not exists idx_market_assets_rarity on market_assets (rarity);


-- ============================================================
-- 3. PRICE HISTORY (rolling, used for charts)
-- Keep last ~1000 snapshots per asset = ~1.4h at 5s tick.
-- ============================================================
create table if not exists market_price_snapshots (
    id          bigserial primary key,
    asset_key   text not null references market_assets(key) on delete cascade,
    price       bigint not null,
    volume      bigint not null default 0,
    ts          timestamptz not null default now()
);
create index if not exists idx_market_snap_asset_time
    on market_price_snapshots (asset_key, ts desc);


-- ============================================================
-- 4. PLAYER HOLDINGS
-- Quantity in MICROUNITS (×1,000,000) so 0.000001 BTC = 1 unit stored.
-- Bigint вмещает до 9.2×10^18, overflow невозможен.
-- ============================================================
create table if not exists market_holdings (
    user_id        bigint not null references users(tg_id) on delete cascade,
    asset_key      text   not null references market_assets(key) on delete cascade,
    quantity       bigint not null,                -- microunits (×1,000,000)
    avg_buy_price  bigint not null,                -- weighted avg, in TRYLLA cents
    last_traded_at timestamptz not null default now(),
    primary key (user_id, asset_key)
);
create index if not exists idx_market_hold_user on market_holdings (user_id);


-- ============================================================
-- 5. TRADE HISTORY (rolling, last 500 per user)
-- ============================================================
create table if not exists market_trades (
    id           bigserial primary key,
    user_id      bigint not null references users(tg_id) on delete cascade,
    asset_key    text   not null,
    side         text   not null,                  -- 'buy' or 'sell'
    quantity     bigint not null,                  -- microunits (×1,000,000)
    price        bigint not null,                  -- per-unit fill price (TRYLLA cents)
    total_value  bigint not null,                  -- TRYLLA cents
    commission   bigint not null default 0,
    realized_pl  bigint not null default 0,        -- only for sells
    ts           timestamptz not null default now()
);
create index if not exists idx_market_trades_user on market_trades (user_id, ts desc);


-- ============================================================
-- 6. NEWS FEED
-- Active news influence prices via affected JSONB (asset_key → pct/min).
-- ============================================================
create table if not exists market_news (
    id          bigserial primary key,
    headline    text   not null,
    body        text,
    type        text   not null,                   -- positive/negative/rumor/regulation/crash/...
    severity    text   not null default 'medium',  -- light/medium/heavy
    -- {asset_key: pct_change_per_minute} OR {category:"crypto", pct: -5}
    affected    jsonb  not null default '{}'::jsonb,
    duration_sec int   not null default 600,
    -- If set, schedules a follow-up news to spawn after delay_sec
    cascade_news_key text,
    cascade_delay_sec int default 0,
    spawned_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);
create index if not exists idx_market_news_active on market_news (expires_at desc);


-- ============================================================
-- 7. ORDERS (Phase 3 — limit / stop / take-profit)
-- ============================================================
create table if not exists market_orders (
    id            bigserial primary key,
    user_id       bigint not null references users(tg_id) on delete cascade,
    asset_key     text   not null,
    order_type    text   not null,                 -- 'limit_buy' / 'limit_sell' / 'stop_loss' / 'take_profit'
    side          text   not null,                 -- 'buy' or 'sell'
    target_price  bigint not null,
    quantity      bigint not null,
    status        text   not null default 'open',  -- open / filled / cancelled
    placed_at     timestamptz not null default now(),
    filled_at     timestamptz
);
create index if not exists idx_market_orders_active on market_orders (status, asset_key)
    where status = 'open';


-- ============================================================
-- 8. PORTFOLIO SUBSCRIPTIONS
-- Players can pay TRYLLA to view top traders' portfolios for 24h.
-- ============================================================
create table if not exists market_subscriptions (
    id              bigserial primary key,
    subscriber_id   bigint not null references users(tg_id) on delete cascade,
    target_id       bigint not null references users(tg_id) on delete cascade,
    paid            bigint not null default 0,
    starts_at       timestamptz not null default now(),
    expires_at      timestamptz not null,
    unique (subscriber_id, target_id, expires_at)
);
create index if not exists idx_market_subs_active
    on market_subscriptions (subscriber_id, expires_at desc);


-- ============================================================
-- 9. WHALE ACTIONS (server-driven big trades that move markets)
-- Visible to players with Whale Tracker skill before they execute.
-- ============================================================
create table if not exists market_whale_actions (
    id            bigserial primary key,
    asset_key     text   not null,
    action        text   not null,                 -- 'accumulate' / 'distribute' / 'pump' / 'dump'
    magnitude     real   not null,                 -- impact pct
    visible_at    timestamptz not null default now(),  -- public sees at this time
    insider_at    timestamptz,                      -- skilled players see at this time (earlier)
    executes_at   timestamptz not null,
    completed     boolean not null default false
);
create index if not exists idx_market_whale_active
    on market_whale_actions (executes_at)
    where completed = false;


-- ============================================================
-- 10. BANK LOANS — спасение для разорившихся игроков
-- Можно взять кредит, % начисляется ежедневно. Если не платить —
-- штрафные дни. Multiple loans разрешены (но cap по уровню).
-- ============================================================
create table if not exists market_loans (
    id              bigserial primary key,
    user_id         bigint not null references users(tg_id) on delete cascade,
    principal       bigint not null,           -- сколько взяли (TRYLLA cents)
    daily_rate      real   not null,           -- ставка в день (например 0.05 = 5%)
    days_accrued    int    not null default 0, -- сколько дней процентов уже начислено
    accrued_interest bigint not null default 0,-- начисленные проценты (cents)
    repaid          bigint not null default 0, -- сколько уже погашено
    taken_at        timestamptz not null default now(),
    due_at          timestamptz not null,      -- срок погашения (taken_at + N days)
    status          text   not null default 'active', -- active/paid/defaulted
    overdue_days    int    not null default 0  -- сколько дней просрочки (для штрафа)
);
create index if not exists idx_market_loans_active on market_loans (user_id, status)
    where status = 'active';
