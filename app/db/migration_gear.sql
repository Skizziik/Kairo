-- Migration: Gear (equipment) system — items bought for coins that equip to 7 slots
-- and provide permanent passive buffs inside Forge and across the casino.
-- Idempotent.

alter table forge_users
  add column if not exists gear_affixes jsonb not null default '{}'::jsonb;

create table if not exists gear_inventory (
    id           bigserial primary key,
    tg_id        bigint not null references users(tg_id) on delete cascade,
    item_key     text not null,                          -- catalog key, e.g. "helmet_kairo_crown"
    slot         text not null,                          -- "helmet"/"armor"/"boots"/"gloves"/"ring"/"amulet"/"drone"
    equipped     boolean not null default false,
    acquired_at  timestamptz not null default now()
);

create index if not exists idx_gear_tg on gear_inventory (tg_id);
-- Only ONE equipped item per (user, slot).
create unique index if not exists idx_gear_one_equipped_per_slot
    on gear_inventory (tg_id, slot)
    where equipped;
