-- Migration: lifetime wager tracking for the casino tier/rank system.
-- Idempotent. Adds a single bigint column to economy_users that accumulates
-- every casino bet (bet, not net) regardless of win/loss. The tier badge
-- displayed in profile + leaderboard is derived purely from this number.

alter table economy_users
    add column if not exists lifetime_wager bigint not null default 0;
