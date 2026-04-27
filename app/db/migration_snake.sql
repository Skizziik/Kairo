-- Migration: Snake mini-game state. Idempotent.
--
-- snake_users — per-player progression. Single row per player.
-- Most lists/maps live in JSONB columns so we can iterate the design fast
-- without endless ALTER TABLE migrations for each new upgrade or skin.

create table if not exists snake_users (
    tg_id              bigint primary key references users(tg_id) on delete cascade,
    level              int    not null default 1,
    xp                 bigint not null default 0,

    -- Lifetime stats (drive leaderboard + achievements)
    coins_lifetime     bigint not null default 0,
    runs_count         int    not null default 0,
    total_skins_eaten  int    not null default 0,
    best_run_coins     bigint not null default 0,
    best_run_length    int    not null default 0,

    -- AFK farm bookkeeping
    daily_afk_earned   bigint    not null default 0,
    daily_afk_day      date,
    last_afk_tick_at   timestamptz,

    -- Cosmetics + map selection
    current_skin_id    text  not null default 'default',
    owned_skins        jsonb not null default '["default"]'::jsonb,
    current_map_id     text  not null default 'park',
    unlocked_maps      jsonb not null default '["park"]'::jsonb,

    -- Upgrade levels: {upgrade_key: int_level}
    upgrades           jsonb not null default '{}'::jsonb,

    -- AFK farm: {snake_key: [level_of_copy_0, level_of_copy_1, ...]}
    -- Length of array = number of copies owned. Each int = upgrade level for THAT copy.
    afk_snakes         jsonb not null default '{}'::jsonb,

    -- Achievements
    achievements       jsonb not null default '[]'::jsonb,

    last_run_at        timestamptz,
    created_at         timestamptz not null default now()
);

create index if not exists idx_snake_users_lvl_xp on snake_users (level desc, xp desc);
create index if not exists idx_snake_users_lifetime on snake_users (coins_lifetime desc);
create index if not exists idx_snake_users_best_run on snake_users (best_run_coins desc);

-- Per-run history (last ~50 runs per user retained for stats display).
-- Aggregate stats live on snake_users; this is for graphs / replay only.
create table if not exists snake_runs (
    id            bigserial primary key,
    user_id       bigint not null references users(tg_id) on delete cascade,
    coins         bigint not null,
    length        int    not null,
    skins_eaten   int    not null,
    duration_sec  int    not null,
    mode          text   not null,
    map_id        text   not null,
    died_to       text,           -- "wall" | "self" | "obstacle" | "timeout" | "manual"
    created_at    timestamptz not null default now()
);
create index if not exists idx_snake_runs_user_time on snake_runs (user_id, created_at desc);
create index if not exists idx_snake_runs_coins on snake_runs (coins desc);
