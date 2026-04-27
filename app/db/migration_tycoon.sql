-- Migration: Casino Tycoon — build-your-own-casino tycoon game.
-- Three-currency model:
--   chips ($T) — internal grind currency, earned from slot/table income
--   cash ($)   — mid-tier currency, converted from chips via cashier
--   coins (🪙) — main app currency, converted from cash at bank (daily cap)
--   vip_stars  — prestige tokens earned only via full reset
-- Idempotent (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

create table if not exists tycoon_state (
    user_id              bigint primary key references users(tg_id) on delete cascade,
    chips                bigint  not null default 0,
    cash                 bigint  not null default 1000,        -- starting bankroll
    vip_stars            int     not null default 0,
    floor_capacity       int     not null default 6,           -- # of unit slots unlocked
    reputation_stars     real    not null default 1.0,         -- 0..5
    theme                text    not null default 'vegas',
    last_tick_at         timestamptz not null default now(),
    last_bank_conv_day   date,
    bank_conv_today      bigint  not null default 0,           -- coins converted today (vs daily cap)
    total_lifetime_cash  bigint  not null default 0,           -- progression metric
    prestige_count       int     not null default 0,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

-- Catalog of buyable units (slots, tables, amenities). Static data — seeded
-- by the application on startup, can grow in future migrations.
create table if not exists tycoon_units_catalog (
    key                  text primary key,
    name                 text  not null,
    kind                 text  not null,                       -- slot | table | amenity | bot_post
    tier                 int   not null default 1,
    cost_cash            bigint not null default 0,            -- buy in cash
    cost_chips           bigint not null default 0,            -- some early units cost chips
    base_chips_per_sec   real  not null default 0,             -- income while occupied
    capacity             int   not null default 1,             -- max simultaneous visitors
    unlock_reputation    real  not null default 0,             -- min stars to unlock
    icon                 text,                                  -- legacy/short-name fallback
    description          text
);

-- Player-owned units placed on the floor.
create table if not exists tycoon_units (
    id              bigserial primary key,
    user_id         bigint not null references users(tg_id) on delete cascade,
    unit_key        text   not null references tycoon_units_catalog(key) on delete cascade,
    cell_x          int    not null,
    cell_y          int    not null,
    level           int    not null default 1,                  -- per-unit upgrade
    chips_in_tray   bigint not null default 0,                  -- pending chips to collect
    occupancy_pct   real   not null default 0,                  -- 0..100, % of time occupied
    last_collected_at timestamptz not null default now(),
    placed_at       timestamptz not null default now()
);
create index if not exists idx_tycoon_units_user on tycoon_units (user_id);

-- Hired staff bots. Salary deducted on AFK tick. Each bot has a kind that
-- determines its effect (cashier auto-converts, attendant auto-collects, etc.).
create table if not exists tycoon_bots (
    id              bigserial primary key,
    user_id         bigint not null references users(tg_id) on delete cascade,
    kind            text   not null,                            -- cashier | slot_attendant | dealer | manager | guard
    level           int    not null default 1,
    hired_at        timestamptz not null default now()
);
create index if not exists idx_tycoon_bots_user on tycoon_bots (user_id);

-- Decoration slots — display CS skins from main inventory in casino lobbies
-- for reputation bonuses. inv_id references economy_inventory.id.
create table if not exists tycoon_decor (
    id              bigserial primary key,
    user_id         bigint not null references users(tg_id) on delete cascade,
    inv_id          bigint not null,                            -- soft FK (skin can be deleted/sold normally)
    slot_index      int    not null,                            -- display position
    placed_at       timestamptz not null default now()
);
create index if not exists idx_tycoon_decor_user on tycoon_decor (user_id);
