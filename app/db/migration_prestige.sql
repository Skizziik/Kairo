-- Migration: Forge Prestige system (reset progress for permanent bonuses).
-- Idempotent via `IF NOT EXISTS`.

alter table forge_users
  add column if not exists prestige_level        int    not null default 0,
  add column if not exists jetons                int    not null default 0,
  add column if not exists jetons_lifetime       int    not null default 0,
  add column if not exists run_particles_earned  bigint not null default 0,
  add column if not exists hammer_power_lvl      int    not null default 0,
  add column if not exists dust_magic_lvl        int    not null default 0,
  add column if not exists bot_tune_lvl          int    not null default 0,
  add column if not exists sharpen_lvl           int    not null default 0,
  add column if not exists fortune_lvl           int    not null default 0,
  add column if not exists starting_capital_lvl  int    not null default 0,
  add column if not exists discount_lvl          int    not null default 0,
  add column if not exists case_face_lvl         int    not null default 0;

-- Backfill run_particles_earned for existing users so their next prestige isn't instant-gold.
-- Setting to 0 is the safest default — they start the prestige journey fresh.
update forge_users
  set run_particles_earned = 0
where run_particles_earned is null;
