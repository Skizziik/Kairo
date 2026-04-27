-- Migration: per-bet audit log for casino activity (7-day retention).
-- Idempotent. Independent from economy_transactions — that one tracks coin
-- movement; this one tracks game-level detail (paths, paths, picks, etc.)
-- so we can answer "what exactly did Lagurman play in the last hour".

create table if not exists bet_audit (
    id              bigserial primary key,
    user_id         bigint not null references users(tg_id) on delete cascade,
    game            text   not null,                  -- coinflip|slots|crash|megaslot|mines|plinko|cf_pvp|wheel
    bet             bigint not null,                  -- wager (positive)
    win             bigint not null default 0,        -- gross winnings (0 on loss)
    net             bigint not null,                  -- net delta (signed: negative = loss)
    details         jsonb  not null default '{}'::jsonb,
    balance_after   bigint,
    created_at      timestamptz not null default now()
);

-- Hot index for "give me Lagurman's last X bets" queries
create index if not exists idx_bet_audit_user_time on bet_audit (user_id, created_at desc);
-- Index for the cleanup pass
create index if not exists idx_bet_audit_time on bet_audit (created_at);
-- Index for "all CS Gates bonus_buys this week" queries
create index if not exists idx_bet_audit_game on bet_audit (game);
