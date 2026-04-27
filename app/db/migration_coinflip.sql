-- Migration: Skin Coinflip 1v1 (PvP)
-- Two players each stake skins; server rolls 50/50; winner takes both stacks.
-- Idempotent — safe to re-run.

create table if not exists coinflip_lobbies (
    id              bigserial primary key,
    creator_id      bigint  not null references users(tg_id) on delete cascade,
    creator_skins   jsonb   not null,                  -- list of inventory ids
    creator_value   bigint  not null,                  -- sum of skin prices when locked
    opponent_id     bigint  references users(tg_id) on delete set null,
    opponent_skins  jsonb,                             -- list of inventory ids when joined
    opponent_value  bigint,
    status          text    not null default 'open',   -- open | matched | settled | cancelled | expired
    winner_id       bigint  references users(tg_id) on delete set null,
    pot_value       bigint,                            -- creator_value + opponent_value at settle
    server_seed     text,                              -- random seed used for the roll (provably fair)
    roll_value      double precision,                  -- 0..1 random number that decided the winner
    rolled_at       timestamptz,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null default (now() + interval '24 hours'),
    invited_to_chat boolean not null default false     -- creator can spam-share once
);

create index if not exists idx_cf_lobbies_status on coinflip_lobbies (status, expires_at desc);
create index if not exists idx_cf_lobbies_creator on coinflip_lobbies (creator_id);

-- Lock skins to a specific lobby. Prevents selling / market-listing / using in
-- another lobby. Cleared automatically on settle/cancel.
alter table economy_inventory
    add column if not exists coinflip_lobby_id bigint references coinflip_lobbies(id) on delete set null;

create index if not exists idx_inventory_cf_lock on economy_inventory (coinflip_lobby_id) where coinflip_lobby_id is not null;

-- Remember the chat invitation so it can be deleted once the lobby resolves.
alter table coinflip_lobbies
    add column if not exists invite_chat_id     bigint,
    add column if not exists invite_message_id  bigint;
