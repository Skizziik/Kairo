-- Migration: Boss system — 10 story bosses + endless mode + boss-hunter prestige branch.
-- Idempotent.

alter table forge_users
  add column if not exists boss_tier             int    not null default 1,
  add column if not exists boss_current_hp       bigint not null default 50000,
  add column if not exists boss_total_kills      int    not null default 0,
  add column if not exists boss_max_tier         int    not null default 1,
  add column if not exists boss_endless_kills    int    not null default 0,
  add column if not exists boss_selected_tier    int    not null default 1,
  add column if not exists boss_dmg_lvl          int    not null default 0,
  add column if not exists boss_crit_lvl         int    not null default 0,
  add column if not exists boss_coin_lvl         int    not null default 0,
  add column if not exists boss_double_lvl       int    not null default 0,
  add column if not exists boss_pierce_lvl       int    not null default 0,
  add column if not exists boss_megahit_lvl      int    not null default 0;

-- Per-tier HP table (so player can switch between bosses without losing damage progress)
create table if not exists boss_progress (
  tg_id        bigint not null references users(tg_id) on delete cascade,
  tier         int    not null,
  current_hp   bigint not null,
  kills        int    not null default 0,
  last_attack_at timestamptz,
  cooldown_until timestamptz,
  primary key (tg_id, tier)
);
alter table boss_progress add column if not exists last_attack_at timestamptz;
alter table boss_progress add column if not exists cooldown_until timestamptz;
create index if not exists idx_boss_progress_tg on boss_progress (tg_id);

-- Separate boss-attack counter so the megahit "every Nth tap" cycle isn't
-- driven by global forge clicks. Without this, hammering the forge would
-- pre-rotate the megahit cycle on the boss tab.
alter table forge_users add column if not exists boss_attack_count bigint not null default 0;
