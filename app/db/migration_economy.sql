-- Migration: Kairo virtual economy (casino, skins, cases, inventory).
-- Idempotent, safe to re-run.

create table if not exists economy_users (
    tg_id              bigint primary key references users(tg_id) on delete cascade,
    balance            bigint not null default 0,
    total_earned       bigint not null default 0,
    total_spent        bigint not null default 0,
    cases_opened       bigint not null default 0,
    current_streak     int not null default 0,
    best_streak        int not null default 0,
    last_daily_at      timestamptz,
    activity_earned_today int not null default 0,  -- cap per day
    activity_day       date,
    created_at         timestamptz not null default now()
);

-- Catalog of skin templates (one row per unique weapon+skin combo).
create table if not exists economy_skins_catalog (
    id           serial primary key,
    key          text not null unique,      -- e.g. "ak-47_redline"
    weapon       text not null,             -- "AK-47"
    skin_name    text not null,             -- "Redline"
    full_name    text not null,             -- "AK-47 | Redline"
    rarity       text not null,             -- consumer/industrial/mil-spec/restricted/classified/covert/exceedingly_rare
    rarity_color text not null,             -- hex
    category     text not null default 'weapon',  -- weapon / knife / gloves
    min_float    real not null default 0.00,
    max_float    real not null default 1.00,
    image_url    text not null,
    base_price   bigint not null,           -- in coins, Field-Tested normal as reference
    stat_trak_available boolean not null default true,
    active       boolean not null default true,
    created_at   timestamptz not null default now()
);

create index if not exists idx_skins_rarity on economy_skins_catalog (rarity) where active;

-- 5 themed cases (seeded separately).
create table if not exists economy_cases (
    id           serial primary key,
    key          text not null unique,      -- e.g. "igor_king_of_mid"
    name         text not null,
    description  text,
    price        bigint not null,
    image_url    text,                      -- cover art
    loot_pool    jsonb not null,            -- [{skin_id, weight, rarity_boost}]
    stat_trak_chance real not null default 0.05,
    active       boolean not null default true,
    created_at   timestamptz not null default now()
);

-- Items in user inventories. Each is an instance with its own float/stattrak.
create table if not exists economy_inventory (
    id              bigserial primary key,
    user_id         bigint not null references users(tg_id) on delete cascade,
    skin_id         int not null references economy_skins_catalog(id),
    float_value     real not null,
    wear            text not null,          -- factory_new / minimal_wear / field_tested / well_worn / battle_scarred
    stat_trak       boolean not null default false,
    price           bigint not null,        -- computed price at acquisition time
    source          text not null default 'case',  -- case/trade/market/gift/upgrade
    source_ref      text,                   -- case_id, trade_id, etc.
    locked          boolean not null default false,  -- true when listed on market or in trade
    acquired_at     timestamptz not null default now()
);

create index if not exists idx_inventory_user on economy_inventory (user_id) where not locked;

-- Ledger for all coin movements (for debug + future leaderboards).
create table if not exists economy_transactions (
    id          bigserial primary key,
    user_id     bigint not null references users(tg_id) on delete cascade,
    amount      bigint not null,            -- positive = earned, negative = spent
    kind        text not null,              -- daily/activity/quiz/task/case/trade/market/gift/upgrade/casino/admin
    reason      text,
    ref_id      bigint,
    balance_after bigint not null,
    created_at  timestamptz not null default now()
);

create index if not exists idx_tx_user_recent on economy_transactions (user_id, created_at desc);

-- User's public showcase for profile (up to 6 favorite items).
create table if not exists economy_showcase (
    user_id    bigint primary key references users(tg_id) on delete cascade,
    item_ids   jsonb not null default '[]'::jsonb,  -- list of inventory ids in order
    updated_at timestamptz not null default now()
);

-- Trade offers between users.
create table if not exists economy_trades (
    id              bigserial primary key,
    from_user       bigint not null references users(tg_id) on delete cascade,
    to_user         bigint not null references users(tg_id) on delete cascade,
    offer_items     jsonb not null default '[]'::jsonb,   -- list of inventory ids from from_user
    request_items   jsonb not null default '[]'::jsonb,   -- list of inventory ids from to_user
    offer_coins     bigint not null default 0,
    request_coins   bigint not null default 0,
    message         text,
    status          text not null default 'pending',      -- pending/accepted/declined/cancelled/expired
    created_at      timestamptz not null default now(),
    resolved_at     timestamptz
);

create index if not exists idx_trades_incoming on economy_trades (to_user, status);
create index if not exists idx_trades_outgoing on economy_trades (from_user, status);

-- Market listings.
create table if not exists economy_market (
    id              bigserial primary key,
    seller_id       bigint not null references users(tg_id) on delete cascade,
    inventory_id    bigint not null unique references economy_inventory(id) on delete cascade,
    price           bigint not null,
    status          text not null default 'active',   -- active/sold/cancelled
    created_at      timestamptz not null default now(),
    sold_to         bigint references users(tg_id),
    sold_at         timestamptz
);

create index if not exists idx_market_active on economy_market (status, created_at desc) where status='active';
