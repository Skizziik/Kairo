"""CS:Clicker schema migrations. Idempotent — runs at startup."""
from __future__ import annotations

import logging

from app.db.client import pool

log = logging.getLogger(__name__)


SCHEMA_SQL = """
create table if not exists clicker_users (
    tg_id            bigint primary key,
    username         text,
    first_name       text,
    last_name        text,
    is_premium       boolean not null default false,
    perma_buffs        jsonb not null default '{}'::jsonb,
    boss_no_chest_streak integer not null default 0,

    -- progression
    level            integer not null default 1,
    max_level        integer not null default 1,
    checkpoint       integer not null default 1,

    -- currencies (NUMERIC for big-int safety)
    cash             numeric not null default 0,         -- $ Баксы (soft)
    casecoins        numeric not null default 0,         -- ⌬ premium
    glory            numeric not null default 0,         -- ★ prestige
    bp_xp            numeric not null default 0,         -- weekly battlepass

    -- combat stats (cached server-side; recomputed when upgrades change)
    click_damage     numeric not null default 1,
    auto_dps         numeric not null default 0,
    crit_chance      numeric not null default 0,
    crit_multiplier  numeric not null default 2,
    luck             numeric not null default 0,

    -- meta
    prestige_count   integer not null default 0,
    artifact_slots   integer not null default 2,         -- starts 2, +1 per prestige (max 6)
    bosses_killed    integer not null default 0,
    chests_opened    integer not null default 0,
    total_damage     numeric not null default 0,
    casecoins_today  integer not null default 0,
    casecoins_day    date,                                -- which UTC date the counter belongs to

    -- timing
    last_seen_at     timestamptz not null default now(),
    last_idle_at     timestamptz not null default now(),
    last_combat_at   timestamptz,
    level_started_at timestamptz not null default now(),
    online_seconds   integer not null default 0,         -- total online time for casecoin rate

    -- moderation
    banned           boolean not null default false,
    ban_reason       text,

    created_at       timestamptz not null default now()
);

create index if not exists idx_clicker_users_max_level
    on clicker_users(max_level desc) where banned = false;
create index if not exists idx_clicker_users_cash
    on clicker_users(cash desc) where banned = false;
create index if not exists idx_clicker_users_glory
    on clicker_users(glory desc) where banned = false;

-- Per-user ownership of enemy HP for the current level (so HP persists between sessions).
create table if not exists clicker_combat_state (
    tg_id            bigint primary key references clicker_users(tg_id) on delete cascade,
    enemy_hp         numeric not null default 0,
    enemy_max_hp     numeric not null default 0,
    is_boss          boolean not null default false,
    timer_ends_at    timestamptz,
    mechanic_state   jsonb not null default '{}'::jsonb,
    updated_at       timestamptz not null default now()
);

-- Player upgrade ownership. Generic across all upgrade kinds.
-- kind = 'weapon' | 'merc' | 'crit' | 'luck' | 'biz' | 'prestige'
create table if not exists clicker_upgrades (
    tg_id            bigint not null references clicker_users(tg_id) on delete cascade,
    kind             text not null,
    slot_id          text not null,                       -- e.g. 'weapon_01' / 'merc_03' / 'biz_shop_branch_1'
    level            integer not null default 0,
    primary key (tg_id, kind, slot_id)
);

create index if not exists idx_clicker_upgrades_user
    on clicker_upgrades(tg_id);

-- Inventory: chests (sealed/opened), artifacts (with equipped slot 0..5), mythics.
create table if not exists clicker_inventory (
    id               bigserial primary key,
    tg_id            bigint not null references clicker_users(tg_id) on delete cascade,
    item_kind        text not null,                       -- 'chest' | 'artifact' | 'mythic'
    item_id          text not null,                       -- e.g. 'chest_common' | 'artifact_07_01' | 'mythic_01'
    rarity           text,                                -- 'common'..'mythic'
    equipped_slot    smallint,                            -- NULL or 0..5 for artifacts
    metadata         jsonb not null default '{}'::jsonb,
    acquired_at      timestamptz not null default now(),
    consumed_at      timestamptz                          -- e.g. when chest is opened
);

create index if not exists idx_clicker_inventory_user
    on clicker_inventory(tg_id);
create index if not exists idx_clicker_inventory_user_kind
    on clicker_inventory(tg_id, item_kind) where consumed_at is null;
create index if not exists idx_clicker_inventory_equipped
    on clicker_inventory(tg_id, equipped_slot) where equipped_slot is not null;

-- Resources from businesses (Phase 2).
create table if not exists clicker_resources (
    tg_id            bigint not null references clicker_users(tg_id) on delete cascade,
    resource_type    text not null,                       -- 'energy' | 'brass' | 'contraband' | 'case_dust' | 'clean_skins' | 'crypto' | 'gas'
    amount           numeric not null default 0,
    primary key (tg_id, resource_type)
);

-- Business state (per-user). Level lives in clicker_upgrades(kind='business').
create table if not exists clicker_businesses (
    tg_id              bigint not null references clicker_users(tg_id) on delete cascade,
    business_id        text not null,
    last_idle_at       timestamptz not null default now(),
    pending_amount     numeric not null default 0,
    primary key (tg_id, business_id)
);

create index if not exists idx_clicker_businesses_user
    on clicker_businesses(tg_id);

-- P2P marketplace lots. Sellers offer X for Y; buyers accept whole.
create table if not exists clicker_lots (
    id              bigserial primary key,
    seller_tg_id    bigint not null references clicker_users(tg_id) on delete cascade,
    -- Offering side
    offer_kind      text not null,                       -- 'resource' | 'artifact' | 'cash' | 'casecoins'
    offer_id        text,                                -- resource_type, item_id, or null for cash/casecoins
    offer_amount    numeric not null,
    offer_payload   jsonb not null default '{}'::jsonb,  -- snapshot for restoration on cancel
    -- Asking side
    ask_kind        text not null,
    ask_id          text,
    ask_amount      numeric not null,
    -- State
    status          text not null default 'active',      -- active | sold | cancelled | expired
    expires_at      timestamptz not null,
    sold_to_tg_id   bigint,
    sold_at         timestamptz,
    cancelled_at    timestamptz,
    seller_name     text,                                -- denormalized for fast listing
    created_at      timestamptz not null default now()
);

create index if not exists idx_clicker_lots_active
    on clicker_lots(status, expires_at) where status = 'active';
create index if not exists idx_clicker_lots_seller
    on clicker_lots(seller_tg_id);
create index if not exists idx_clicker_lots_offer
    on clicker_lots(offer_kind, offer_id) where status = 'active';

-- PvP raids: one player attacks one of another player's businesses.
create table if not exists clicker_raids (
    id              bigserial primary key,
    raider_tg_id    bigint not null,
    victim_tg_id    bigint not null,
    business_id     text not null,
    success         boolean,
    resource_type   text,
    amount_stolen   numeric not null default 0,
    cost_paid       numeric not null default 0,
    success_chance  numeric,
    started_at      timestamptz not null default now()
);
create index if not exists idx_clicker_raids_pair_time
    on clicker_raids(raider_tg_id, victim_tg_id, started_at desc);
create index if not exists idx_clicker_raids_victim
    on clicker_raids(victim_tg_id, started_at desc);

-- PvP duels: async DPS comparison.
create table if not exists clicker_duels (
    id                 bigserial primary key,
    challenger_tg_id   bigint not null,
    challenged_tg_id   bigint not null,
    stake_kind         text not null,
    stake_id           text,
    stake_amount       numeric not null,
    challenger_score   numeric,
    challenged_score   numeric,
    winner_tg_id       bigint,
    commission_paid    numeric not null default 0,
    started_at         timestamptz not null default now()
);
create index if not exists idx_clicker_duels_pair_time
    on clicker_duels(challenger_tg_id, challenged_tg_id, started_at desc);

-- Battle pass progress (weekly). Resets each Monday 00:00 UTC.
create table if not exists clicker_battlepass (
    tg_id            bigint not null references clicker_users(tg_id) on delete cascade,
    week_start       date not null,
    bp_xp            numeric not null default 0,
    bp_level         integer not null default 0,
    premium          boolean not null default false,
    rewards_claimed  integer[] not null default '{}',
    primary key (tg_id, week_start)
);

-- Event log for analytics + anti-cheat.
create table if not exists clicker_event_log (
    id               bigserial primary key,
    tg_id            bigint not null,
    event_type       text not null,
    data             jsonb not null default '{}'::jsonb,
    created_at       timestamptz not null default now()
);

create index if not exists idx_clicker_event_log_user_time
    on clicker_event_log(tg_id, created_at desc);
"""


OFFERS_SQL = """
create table if not exists clicker_offers (
    id              bigserial primary key,
    lot_id          bigint not null references clicker_lots(id) on delete cascade,
    offerer_tg_id   bigint not null references clicker_users(tg_id) on delete cascade,
    offer_payload   jsonb not null default '{}'::jsonb,
    status          text not null default 'pending',
    expires_at      timestamptz not null,
    created_at      timestamptz not null default now(),
    responded_at    timestamptz,
    note            text
);
create index if not exists idx_clicker_offers_lot on clicker_offers(lot_id) where status = 'pending';
create index if not exists idx_clicker_offers_offerer on clicker_offers(offerer_tg_id, created_at desc);
"""


async def ensure_schema() -> None:
    async with pool().acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        # Idempotent: add columns to existing tables if missing.
        await conn.execute(
            "alter table clicker_users add column if not exists perma_buffs jsonb not null default '{}'::jsonb"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists boss_no_chest_streak integer not null default 0"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists tap_counter integer not null default 0"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists last_cleanse_at timestamptz"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists biz_tap_window_start timestamptz"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists biz_tap_window_count integer not null default 0"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists tap_window_start timestamptz"
        )
        await conn.execute(
            "alter table clicker_users add column if not exists tap_window_count integer not null default 0"
        )
        await conn.execute(
            "alter table clicker_combat_state add column if not exists mechanic_state jsonb not null default '{}'::jsonb"
        )
        await conn.execute(
            "alter table clicker_lots add column if not exists multi_assets jsonb"
        )
        await conn.execute(OFFERS_SQL)
    log.info("clicker schema ensured")
