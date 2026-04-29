-- Migration: Налоговая (tax authority) state. Idempotent.
-- One row per player tracking their entity, accumulated tax due, debts,
-- streaks, and special-action cooldowns (amnesty, paradise).

create table if not exists tax_users (
    tg_id                   bigint primary key references users(tg_id) on delete cascade,

    -- Current legal entity (0=Физ.лицо ... 5=Офшор). Drives the base tax rate.
    entity_level            int    not null default 0,

    -- Tax-authority perk levels (jsonb keyed by upgrade_key → int level)
    upgrades                jsonb  not null default '{}'::jsonb,

    -- Income earned since the last hourly tick (gets multiplied by current
    -- effective rate and rolled into pending_tax_due each tick).
    pending_taxable_income  bigint not null default 0,

    -- Total tax owed but not yet paid. Manual `Заплатить` button or the
    -- midnight SET force-collection drains this. Forced collection beyond
    -- what the balance can cover spills into `tax_debt`.
    pending_tax_due         bigint not null default 0,

    -- Forced unpaid debt. Compounds at +10%/day (configurable via Адвокат
    -- perk). While > 0 the player is blocked from buying upgrades/cases/
    -- artifacts until they clear it.
    tax_debt                bigint not null default 0,
    debt_since              timestamptz,

    -- Lifetime stats
    total_taxes_paid        bigint not null default 0,
    total_audits            int    not null default 0,
    total_amnesties         int    not null default 0,

    -- Last hourly tick (used to gate ticks + display "next tick at")
    last_tick_at            timestamptz,

    -- Last midnight SET collection
    last_collected_at       timestamptz,

    -- Daily declaration ("Декларация" button) — gives -1% to next-day rate
    last_declared_at        timestamptz,

    -- Amnesty (monthly): wipes debt for 50% of debt amount
    last_amnesty_at         timestamptz,

    -- Налоговый рай: 7 days of zero tax. Once per real month.
    paradise_until          timestamptz,
    last_paradise_at        timestamptz,

    -- Honest-Citizen streak: consecutive days finished without tax_debt.
    -- 30 days → permanent -1% rate badge.
    streak_punctual_days    int    not null default 0,
    last_streak_check       date,
    honest_citizen          boolean not null default false,

    created_at              timestamptz not null default now()
);

-- Fast lookup of indebted players (for warning banners + nightly cron).
create index if not exists idx_tax_debt_active on tax_users (tax_debt) where tax_debt > 0;


-- ============================================================
-- TAX RAIDS — coop event where players sacrifice 500 skins to
-- shut down the tax office for 2 hours (server-wide).
-- ============================================================

create table if not exists tax_raids (
    id              bigserial primary key,
    initiator_id    bigint references users(tg_id) on delete set null,
    -- 'preparing' (10-min collection window)
    -- 'success'   (raid succeeded; tax office is offline until raid_until)
    -- 'failed'    (deadline passed without enough skins; no effect)
    -- 'expired'   (success window closed — tax office is back online)
    status          text not null default 'preparing',
    started_at      timestamptz not null default now(),
    deadline        timestamptz not null,           -- preparing-phase ends here
    succeeded_at    timestamptz,
    raid_until      timestamptz,                    -- if success: tax disabled until this
    skins_donated   int  not null default 0,
    skins_required  int  not null default 500
);
create index if not exists idx_tax_raids_active on tax_raids (status)
    where status in ('preparing', 'success');
create index if not exists idx_tax_raids_started on tax_raids (started_at desc);


create table if not exists tax_raid_donations (
    raid_id     bigint not null references tax_raids(id) on delete cascade,
    user_id     bigint not null references users(tg_id) on delete cascade,
    skins       int    not null default 0,
    donated_at  timestamptz not null default now(),
    primary key (raid_id, user_id)
);
