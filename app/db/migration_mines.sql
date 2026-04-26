-- Migration: Mines (CS-themed Сапёр) — server-authoritative game state.
-- Idempotent. One active game per user; PK = user_id ensures no double-bet.

create table if not exists casino_mines_games (
    user_id      bigint primary key references economy_users(tg_id) on delete cascade,
    bet          integer not null,
    bombs_count  integer not null,
    bomb_cells   jsonb   not null,         -- list of bomb indices (0..24)
    revealed     jsonb   not null default '[]'::jsonb,
    started_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists idx_casino_mines_started_at on casino_mines_games (started_at);

-- Lifetime stats column on economy_users (optional but useful for achievements/missions).
alter table economy_users
    add column if not exists mines_games_played   bigint not null default 0,
    add column if not exists mines_games_won      bigint not null default 0,
    add column if not exists mines_biggest_win    bigint not null default 0;
