-- Migration: Boss system — 10 story bosses + endless mode + boss-hunter prestige branch.
-- Idempotent.

alter table forge_users
  add column if not exists boss_tier             int    not null default 1,
  add column if not exists boss_current_hp       bigint not null default 50000,
  add column if not exists boss_total_kills      int    not null default 0,
  add column if not exists boss_max_tier         int    not null default 1,
  add column if not exists boss_endless_kills    int    not null default 0,
  add column if not exists boss_dmg_lvl          int    not null default 0,
  add column if not exists boss_crit_lvl         int    not null default 0,
  add column if not exists boss_coin_lvl         int    not null default 0,
  add column if not exists boss_double_lvl       int    not null default 0,
  add column if not exists boss_pierce_lvl       int    not null default 0,
  add column if not exists boss_megahit_lvl      int    not null default 0;
