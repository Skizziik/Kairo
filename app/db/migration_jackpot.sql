-- Migration: Jackpot mini-game (CS-case style spinner pot).
-- Idempotent.
--
-- Round lifecycle: pending → spinning → settled (or → cancelled if <2 deposits).
-- One round at a time; new round spawns automatically after settle/cancel.
-- Provably fair: server_seed_hash committed at round start, server_seed
-- revealed at settle. Anyone can verify SHA256(seed) == hash and recompute
-- the winning ticket.

create table if not exists jackpot_rounds (
    id                bigserial primary key,
    status            text not null default 'pending',  -- pending|spinning|settled|cancelled
    started_at        timestamptz not null default now(),
    deposit_ends_at   timestamptz not null,
    spun_at           timestamptz,
    settled_at        timestamptz,
    total_value       bigint not null default 0,
    winner_id         bigint,
    server_seed       text not null,                    -- hex random — secret until settle
    server_seed_hash  text not null,                    -- SHA256 of seed — public from start
    roll_value        bigint,                           -- the picked ticket number (0..total_value-1)
    -- Pre-computed avatar sequence for the CS-case spinner. ~50 entries; the
    -- winner is placed at a known index. Stored so client renders identical
    -- animation each time and can verify the spin matches the determined winner.
    spin_sequence     jsonb
);
create index if not exists idx_jackpot_rounds_status on jackpot_rounds (status);
create index if not exists idx_jackpot_rounds_started on jackpot_rounds (started_at desc);

create table if not exists jackpot_deposits (
    id              bigserial primary key,
    round_id        bigint not null references jackpot_rounds(id) on delete cascade,
    user_id         bigint not null,                   -- 1 = bot user, otherwise users(tg_id)
    inventory_ids   jsonb not null default '[]'::jsonb, -- list of economy_inventory.id (skins)
    coins           bigint not null default 0,          -- raw coin deposit (in addition to skins)
    value           bigint not null,                    -- total deposited value (skins price + coins)
    color           text not null,                      -- hex color for UI segment
    bot_name        text,                               -- display name for bot deposits ("🤖 Mamba" etc.)
    deposited_at    timestamptz not null default now(),
    is_bot          boolean not null default false
);
create index if not exists idx_jackpot_deposits_round on jackpot_deposits (round_id);
create index if not exists idx_jackpot_deposits_user on jackpot_deposits (user_id, deposited_at desc);

-- Lock column on inventory: when a skin is deposited into a jackpot round it
-- gets jackpot_round_id = X, preventing sells/coinflip-lobby/another-jackpot
-- until the round resolves (transferred to winner or refunded).
alter table economy_inventory
    add column if not exists jackpot_round_id bigint;
create index if not exists idx_inventory_jackpot on economy_inventory (jackpot_round_id)
    where jackpot_round_id is not null;
