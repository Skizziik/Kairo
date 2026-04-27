-- Migration: lifetime wager tracking for the casino tier/rank system.
-- Idempotent. Adds a single bigint column to economy_users that accumulates
-- every casino bet (bet, not net) regardless of win/loss. The tier badge
-- displayed in profile + leaderboard is derived purely from this number.

alter table economy_users
    add column if not exists lifetime_wager bigint not null default 0;

-- One-time backfill flag — players who joined before the wager tracker get
-- their historical activity credited as `lifetime_wager = spent + earned`
-- (close approximation of total bet volume). Runs exactly once per row.
alter table economy_users
    add column if not exists wager_backfilled boolean not null default false;

update economy_users
set lifetime_wager = greatest(lifetime_wager, total_spent + total_earned),
    wager_backfilled = true
where not wager_backfilled;
