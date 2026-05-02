"""Schema migrations for Village Tycoon. Idempotent — runs at startup."""
from __future__ import annotations

import logging

from app.db.client import pool

log = logging.getLogger(__name__)


SCHEMA_SQL = """
create table if not exists villager_users (
    tg_id            bigint primary key,
    username         text,
    first_name       text,
    last_name        text,
    language_code    text default 'ru',
    is_premium       boolean not null default false,

    village_name     text not null default 'Моя деревня',
    era              smallint not null default 1,
    player_level     integer not null default 1,
    experience       bigint not null default 0,

    gems_balance     numeric not null default 0,
    pass_active_until timestamptz,

    banned           boolean not null default false,
    ban_reason       text,

    created_at       timestamptz not null default now(),
    last_seen_at     timestamptz not null default now(),
    last_sync_at     timestamptz not null default now()
);

create index if not exists idx_villager_users_last_seen
    on villager_users(last_seen_at) where banned = false;

create table if not exists villager_buildings (
    id                  bigserial primary key,
    tg_id               bigint not null references villager_users(tg_id) on delete cascade,
    building_type       text not null,
    level               smallint not null default 1,
    position_x          smallint not null,
    position_y          smallint not null,
    status              text not null default 'active',
    finish_at           timestamptz,
    last_collected_at   timestamptz not null default now(),
    created_at          timestamptz not null default now(),
    unique (tg_id, position_x, position_y)
);

create index if not exists idx_villager_buildings_user
    on villager_buildings(tg_id);
create index if not exists idx_villager_buildings_finish
    on villager_buildings(finish_at)
    where status in ('building', 'upgrading');

create table if not exists villager_resources (
    tg_id           bigint not null references villager_users(tg_id) on delete cascade,
    resource_type   text not null,
    amount          numeric not null default 0,
    cap             numeric not null default 1000,
    primary key (tg_id, resource_type)
);

create table if not exists villager_quests_progress (
    tg_id           bigint not null references villager_users(tg_id) on delete cascade,
    quest_id        text not null,
    status          text not null default 'active',
    progress        jsonb not null default '{}'::jsonb,
    started_at      timestamptz not null default now(),
    completed_at    timestamptz,
    claimed_at      timestamptz,
    primary key (tg_id, quest_id)
);

create index if not exists idx_villager_quests_active
    on villager_quests_progress(tg_id) where status = 'active';

create table if not exists villager_event_log (
    id          bigserial primary key,
    tg_id       bigint not null,
    event_type  text not null,
    data        jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists idx_villager_event_log_user_time
    on villager_event_log(tg_id, created_at desc);
"""


async def ensure_schema() -> None:
    async with pool().acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    log.info("villager schema ensured")
