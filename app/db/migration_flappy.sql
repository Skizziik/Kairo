-- Migration: Flappy Bird mini-game state. Idempotent.
--
-- flappy_users — per-player progression. Single row per player.
-- Most lists/maps live in JSONB columns so we can iterate the design fast.

create table if not exists flappy_users (
    tg_id              bigint primary key references users(tg_id) on delete cascade,
    level              int    not null default 1,
    xp                 bigint not null default 0,

    -- Pluma — internal currency, exchanged 1:1 for casino coins (taxable)
    pluma_balance      bigint not null default 0,

    -- Lifetime stats
    pluma_lifetime     bigint not null default 0,
    runs_count         int    not null default 0,
    distance_lifetime  bigint not null default 0,
    best_run_distance  int    not null default 0,
    best_run_pluma     bigint not null default 0,
    best_combo         int    not null default 0,

    -- Bird selection
    current_bird_id    text  not null default 'basic',
    owned_birds        jsonb not null default '["basic"]'::jsonb,

    -- Map selection
    current_map_id     text  not null default 'classic',
    owned_maps         jsonb not null default '["classic"]'::jsonb,

    -- Upgrade levels: {upgrade_key: int_level}
    upgrades           jsonb not null default '{}'::jsonb,

    -- Owned artifacts (from cases): list of artifact_keys
    artifacts          jsonb not null default '[]'::jsonb,

    -- Cosmetic / stats
    cases_opened       int    not null default 0,

    last_run_at        timestamptz,
    created_at         timestamptz not null default now()
);

create index if not exists idx_flappy_users_lifetime on flappy_users (pluma_lifetime desc);
create index if not exists idx_flappy_users_best_run on flappy_users (best_run_distance desc);


-- Per-run history (last ~50 retained for stats/replay).
create table if not exists flappy_runs (
    id            bigserial primary key,
    user_id       bigint not null references users(tg_id) on delete cascade,
    distance      int    not null,
    pluma         bigint not null,
    coins_earned  bigint not null default 0,    -- main casino coins credited if cash-out used
    bird          text   not null,
    map_id        text   not null,
    best_combo    int    not null default 0,
    cashed_out    boolean not null default false,
    cashout_mult  real   not null default 1.0,
    duration_sec  int    not null default 0,
    died_to       text,
    created_at    timestamptz not null default now()
);
create index if not exists idx_flappy_runs_user_time on flappy_runs (user_id, created_at desc);
create index if not exists idx_flappy_runs_distance on flappy_runs (distance desc);
