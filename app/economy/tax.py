"""Tax Authority (Налоговая) — hourly income tax on every earning source.

Players register a legal entity (Физ.лицо / Самозанятый / ИП / ООО / Холдинг /
Офшор) which sets their base tax rate. They can stack 8 upgradeable perks that
lower the rate, raise the income exemption, soften late-payment penalties, and
unlock special actions (амнистия, налоговый рай).

Earnings flow:
1. Every credit to balance from a taxable source calls `accrue_tax(tg_id, amt, kind)`.
2. Hourly background tick converts `pending_taxable_income × effective_rate` into
   `pending_tax_due` (then resets the income accumulator).
3. Player pays manually OR midnight SET (Forced collection) drains pending_tax_due.
4. Insufficient balance at SET → spills into `tax_debt`, which compounds at
   +10%/day (softened by Адвокат perk). Indebted players are blocked from
   buying upgrades/cases/artifacts until cleared.

Creative mechanics:
- 🕵 Random audit (1% per hourly tick): if you have debt, penalty ×2; if clean,
  small cashback from total taxes paid.
- 💼 Amnesty (once/month): wipe full debt for 50% of debt amount.
- 📋 Daily declaration: −1% rate for the next day. Streak-tracked.
- 🏖 Налоговый рай: 7 days of 0% tax (1× / real month).
- 🏆 Honest Citizen: 30 days streak without debt → permanent −1% rate badge.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CONFIG — entities, upgrades, constants
# ============================================================

# Legal-entity tiers. Index = entity_level.
ENTITIES: list[dict] = [
    {
        "level": 0, "key": "individual", "name": "Физ. лицо",     "icon": "👤",
        "rate": 0.30, "reg_fee": 0,
        "desc": "Стандартный режим. Налог 30%. Зарегайся куда повыше при первой возможности.",
    },
    {
        "level": 1, "key": "selfemployed", "name": "Самозанятый", "icon": "🧑‍💻",
        "rate": 0.25, "reg_fee": 50_000_000_000,
        "desc": "Простой режим для одиночных мастеров. Ставка 25%.",
    },
    {
        "level": 2, "key": "ip", "name": "ИП",                    "icon": "💼",
        "rate": 0.16, "reg_fee": 100_000_000_000,
        "desc": "Индивидуальный предприниматель. Ставка 16% + перк «Льготы малого бизнеса» даёт ещё −2%.",
    },
    {
        "level": 3, "key": "ooo", "name": "ООО",                  "icon": "🏢",
        "rate": 0.12, "reg_fee": 250_000_000_000,
        "desc": "Юридическое лицо. Ставка 12% + работают льготы малого бизнеса.",
    },
    {
        "level": 4, "key": "holding", "name": "Холдинг",          "icon": "🏛",
        "rate": 0.06, "reg_fee": 500_000_000_000,
        "desc": "Сложная корпоративная структура. 6%. Включается перк «Оффшорные связи» (доп −3%).",
    },
    {
        "level": 5, "key": "offshore", "name": "Офшор",           "icon": "🌴",
        "rate": 0.04, "reg_fee": 1_000_000_000_000,
        "desc": "Кипр, BVI, Каймановы. Минимальная ставка — 4%. Финальный шаг.",
    },
]
ENTITY_BY_LEVEL = {e["level"]: e for e in ENTITIES}


# Upgrades — same data shape as snake (key → tiers list of (lvl, effect, cost)).
def _build_tiers(max_level: int, effect_fn, cost_fn) -> list[tuple]:
    out = []
    for lvl in range(1, max_level + 1):
        e = effect_fn(lvl)
        if isinstance(e, float):
            e = round(e, 4)
        out.append((lvl, e, int(round(cost_fn(lvl)))))
    return out


UPGRADE_DEFS: dict[str, dict] = {
    "accountant": {
        "name": "📒 Бухгалтер", "icon": "📒",
        "desc": "Снижает ставку налога", "unit": "%",
        # -0.5% per level, max -5% at lvl 10
        "tiers": _build_tiers(10, lambda L: -L * 0.5, lambda L: 1_000_000_000 * (1.6 ** (L - 1))),
    },
    "tax_deduction": {
        "name": "🧾 Налоговый вычет", "icon": "🧾",
        "desc": "Первые X 🪙/час не облагаются", "unit": "🪙/ч",
        # 100K at lvl 1 → ~50M at lvl 10
        "tiers": _build_tiers(10, lambda L: int(100_000 * (1.85 ** (L - 1))),
                              lambda L: 5_000_000_000 * (1.65 ** (L - 1))),
    },
    "lawyer": {
        "name": "⚖ Адвокат", "icon": "⚖",
        "desc": "Снижает штраф просрочки 10%/день → меньше", "unit": "%/день",
        # -1% per level (10% → 5% at lvl 5)
        "tiers": _build_tiers(5, lambda L: 10 - L, lambda L: 5_000_000_000 * (2.0 ** (L - 1))),
    },
    "black_books": {
        "name": "🕶 Чёрная бухгалтерия", "icon": "🕶",
        "desc": "Шанс что налог за час «сольётся»", "unit": "%",
        # 1% per level, max 5% at lvl 5
        "tiers": _build_tiers(5, lambda L: L, lambda L: 20_000_000_000 * (2.0 ** (L - 1))),
    },
    "punctual_cashback": {
        "name": "🎯 Кэшбэк за пунктуальность", "icon": "🎯",
        "desc": "7 дней без долгов → +10% возврат от уплаченных за неделю", "unit": "%",
        "tiers": [(1, 10, 50_000_000_000)],
    },
    "small_biz_relief": {
        "name": "🏪 Льготы малого бизнеса", "icon": "🏪",
        "desc": "Доп −2% к ставке если у тебя ИП или ООО", "unit": "%",
        "tiers": [(1, -2, 25_000_000_000)],
    },
    "offshore_links": {
        "name": "🌐 Оффшорные связи", "icon": "🌐",
        "desc": "Доп −3% к ставке если у тебя Холдинг или Офшор", "unit": "%",
        "tiers": [(1, -3, 100_000_000_000)],
    },
    "tax_paradise": {
        "name": "🏖 Налоговый рай", "icon": "🏖",
        "desc": "Активирует 7 дней нулевого налога", "unit": "дней",
        # Single-shot purchase that activates the 7-day buff. Once per real month.
        "tiers": [(1, 7, 5_000_000_000_000)],
    },
}


# Default base penalty rate (per day on outstanding tax_debt). Reduced by Адвокат.
BASE_DEBT_PENALTY_PER_DAY = 0.10

# Hourly tick window (seconds). Tick is idempotent — running more often than
# this is a no-op for already-ticked players.
HOURLY_TICK_SECONDS = 3_600

# Random-audit chance per hourly tick (1%).
AUDIT_CHANCE_PER_TICK = 0.01

# Honest Citizen: how many consecutive clean days unlock the permanent badge.
HONEST_CITIZEN_DAYS = 30

# Newbie exemption: players whose lifetime `economy_users.total_earned` is below
# this threshold pay zero tax. Lets new players ramp up without a punishing
# tax drag in their first sessions.
NEWBIE_EXEMPTION_THRESHOLD = 1_000_000_000  # 1B lifetime earnings

# RAIDS — coop event to shut down the tax office.
RAID_PREP_SECONDS         = 10 * 60         # 10 minutes preparation window
RAID_SUCCESS_DURATION_SEC = 2 * 60 * 60     # 2 hours of zero-tax on success
RAID_REQUIRED_SKINS       = 500             # total donations needed
RAID_COOLDOWN_HOURS       = 24              # max 1 raid per 24h server-wide
RAID_MIN_DONATION         = 1
RAID_MAX_DONATION_PER_TX  = 100             # safety per single donate call


# ============================================================
# DB / SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_tax.sql"
    if not sql_path.exists():
        log.warning("tax migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
        # Wipe legacy tax_debt — debt system was removed in favor of "balance
        # can go negative" simplification. Idempotent — running twice is a
        # no-op since all rows are already 0.
        wiped = await conn.fetchval(
            "select count(*) from tax_users where tax_debt > 0"
        )
        if wiped and int(wiped) > 0:
            log.info("tax: clearing legacy tax_debt for %d players", wiped)
            await conn.execute(
                "update tax_users set tax_debt = 0, debt_since = NULL where tax_debt > 0"
            )
    log.info("tax schema ensured")


async def ensure_user(tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into tax_users (tg_id) values ($1) on conflict do nothing",
            tg_id,
        )


def _parse_jsonb(val) -> Any:
    if val is None: return None
    if isinstance(val, (dict, list)): return val
    if isinstance(val, str):
        try: return json.loads(val)
        except Exception: return None
    return None


# ============================================================
# RATE COMPUTATION
# ============================================================

def _effective_rate(entity_level: int, upgrades: dict, declared_today: bool,
                    honest_citizen: bool, paradise_active: bool) -> float:
    """Final tax rate applied to the player's income.

    Stacking order (additive minus on top of base entity rate):
    - Налоговый рай (7-day buff) → 0% absolute
    - Бухгалтер: −0.5% per level
    - Льготы малого бизнеса: −2% if ИП or ООО
    - Оффшорные связи: −3% if Холдинг or Офшор
    - Декларация (today): −1% for the day
    - Honest Citizen: −1% permanent
    Floor: never below 1% (even офшор + max upgrades stays slightly taxable).
    """
    if paradise_active:
        return 0.0

    entity = ENTITY_BY_LEVEL.get(entity_level, ENTITIES[0])
    rate = float(entity["rate"])

    accountant_lvl = int((upgrades or {}).get("accountant", 0))
    rate -= accountant_lvl * 0.005

    if int((upgrades or {}).get("small_biz_relief", 0)) >= 1 and entity_level in (2, 3):
        rate -= 0.02
    if int((upgrades or {}).get("offshore_links", 0)) >= 1 and entity_level in (4, 5):
        rate -= 0.03
    if declared_today:
        rate -= 0.01
    if honest_citizen:
        rate -= 0.01

    return max(0.01, rate)


def _income_exemption(upgrades: dict) -> int:
    """Налоговый вычет — first X 🪙/hour are exempted."""
    lvl = int((upgrades or {}).get("tax_deduction", 0))
    if lvl <= 0:
        return 0
    return int(100_000 * (1.85 ** (lvl - 1)))


def _debt_penalty_rate(upgrades: dict) -> float:
    """Per-day debt-growth rate. Адвокат softens this."""
    lawyer_lvl = int((upgrades or {}).get("lawyer", 0))
    return max(0.01, BASE_DEBT_PENALTY_PER_DAY - lawyer_lvl * 0.01)


def _black_books_chance(upgrades: dict) -> float:
    return int((upgrades or {}).get("black_books", 0)) * 0.01


# ============================================================
# READ STATE
# ============================================================

async def get_state(tg_id: int) -> dict:
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from tax_users where tg_id = $1", tg_id,
        )
        # Lifetime earnings — drives the newbie exemption.
        eu_row = await conn.fetchrow(
            "select total_earned from economy_users where tg_id = $1", tg_id,
        )
    if row is None:
        return {}
    total_earned = int(eu_row["total_earned"]) if eu_row else 0
    is_newbie = total_earned < NEWBIE_EXEMPTION_THRESHOLD

    upgrades = _parse_jsonb(row["upgrades"]) or {}
    today = datetime.now(timezone.utc).date()
    declared_today = (row["last_declared_at"] is not None and
                      row["last_declared_at"].date() == today)
    paradise_until = row["paradise_until"]
    paradise_active = paradise_until is not None and paradise_until > datetime.now(timezone.utc)

    entity = ENTITY_BY_LEVEL.get(int(row["entity_level"]), ENTITIES[0])
    rate = _effective_rate(
        int(row["entity_level"]), upgrades,
        declared_today=declared_today,
        honest_citizen=bool(row["honest_citizen"]),
        paradise_active=paradise_active,
    )
    # Newbie exemption — overrides everything to 0 until threshold reached.
    if is_newbie:
        rate = 0.0

    # Estimate of how much tax will be charged at the NEXT hourly tick.
    # Useful for UI: "к списанию через час: X 🪙".
    pending_income = int(row["pending_taxable_income"])
    exemption = _income_exemption(upgrades)
    taxable_now = max(0, pending_income - exemption)
    next_tick_tax_estimate = int(taxable_now * rate)

    # Amnesty cooldown — once per real month (30 days)
    last_amnesty = row["last_amnesty_at"]
    amnesty_available = last_amnesty is None or (
        (datetime.now(timezone.utc) - last_amnesty).total_seconds() >= 30 * 24 * 3600
    )

    return {
        "tg_id": int(row["tg_id"]),
        "entity_level": int(row["entity_level"]),
        "entity_name": entity["name"],
        "entity_icon": entity["icon"],
        "base_rate":   entity["rate"],
        "effective_rate": round(rate, 4),
        "rate_breakdown": _rate_breakdown(int(row["entity_level"]), upgrades,
                                          declared_today, bool(row["honest_citizen"]),
                                          paradise_active, is_newbie),
        "upgrades": upgrades,
        "pending_taxable_income": int(row["pending_taxable_income"]),
        "pending_tax_due":        int(row["pending_tax_due"]),
        "tax_debt":               int(row["tax_debt"]),
        "debt_since":              row["debt_since"].isoformat() if row["debt_since"] else None,
        "total_taxes_paid":       int(row["total_taxes_paid"]),
        "total_audits":           int(row["total_audits"]),
        "total_amnesties":        int(row["total_amnesties"]),
        "last_tick_at":           row["last_tick_at"].isoformat() if row["last_tick_at"] else None,
        "last_collected_at":      row["last_collected_at"].isoformat() if row["last_collected_at"] else None,
        "declared_today":         declared_today,
        "amnesty_available":      amnesty_available,
        "paradise_active":        paradise_active,
        "paradise_until":         paradise_until.isoformat() if paradise_until else None,
        "honest_citizen":         bool(row["honest_citizen"]),
        "streak_punctual_days":   int(row["streak_punctual_days"]),
        "income_exemption_per_hour": _income_exemption(upgrades),
        "debt_penalty_per_day":     _debt_penalty_rate(upgrades),
        "black_books_chance":       _black_books_chance(upgrades),
        "next_tick_tax_estimate":   next_tick_tax_estimate,
        "is_newbie":                is_newbie,
        "total_earned":             total_earned,
        "newbie_threshold":         NEWBIE_EXEMPTION_THRESHOLD,
    }


def _rate_breakdown(entity_level: int, upgrades: dict, declared_today: bool,
                     honest_citizen: bool, paradise_active: bool,
                     is_newbie: bool = False) -> list[dict]:
    """Itemized tax rate so the UI can show 'why your rate is what it is'."""
    if is_newbie:
        return [{"label": "🆓 Налоговые каникулы (до 1B)", "value": "0%", "color": "#5cc15c"}]
    if paradise_active:
        return [{"label": "🏖 Налоговый рай", "value": "0%", "color": "#5cc15c"}]
    entity = ENTITY_BY_LEVEL.get(entity_level, ENTITIES[0])
    out = [{"label": f"{entity['icon']} {entity['name']}", "value": f"{entity['rate']*100:.0f}%"}]
    accountant = int((upgrades or {}).get("accountant", 0))
    if accountant > 0:
        out.append({"label": "📒 Бухгалтер", "value": f"−{accountant * 0.5:.1f}%"})
    if int((upgrades or {}).get("small_biz_relief", 0)) >= 1 and entity_level in (2, 3):
        out.append({"label": "🏪 Льготы малого бизнеса", "value": "−2%"})
    if int((upgrades or {}).get("offshore_links", 0)) >= 1 and entity_level in (4, 5):
        out.append({"label": "🌐 Оффшорные связи", "value": "−3%"})
    if declared_today:
        out.append({"label": "📋 Декларация подана", "value": "−1%"})
    if honest_citizen:
        out.append({"label": "🏆 Honest Citizen (30 дней без долгов)", "value": "−1%"})
    return out


async def get_config() -> dict:
    return {
        "entities": ENTITIES,
        "upgrades": [
            {"key": k, "name": v["name"], "icon": v["icon"], "desc": v["desc"],
             "unit": v["unit"], "tiers": v["tiers"], "max_level": len(v["tiers"])}
            for k, v in UPGRADE_DEFS.items()
        ],
        "honest_citizen_days": HONEST_CITIZEN_DAYS,
        "audit_chance_per_tick": AUDIT_CHANCE_PER_TICK,
        "base_debt_penalty_per_day": BASE_DEBT_PENALTY_PER_DAY,
    }


# ============================================================
# ACCRUE TAX (called from earning sources)
# ============================================================

async def accrue_tax(tg_id: int, amount: int, kind: str) -> None:
    """Add `amount` to player's pending_taxable_income. Called from snake/jackpot/etc.
    Defers all rate computation to the hourly tick — keeps source-side code simple.
    Skipped entirely when a successful raid is currently shutting down the office."""
    if amount <= 0:
        return
    try:
        # Single combined query: only insert/update if the tax office is open.
        # Cheaper than a separate is_tax_office_open() round-trip.
        await ensure_user(tg_id)
        async with pool().acquire() as conn:
            await conn.execute(
                """
                update tax_users
                set pending_taxable_income = pending_taxable_income + $2
                where tg_id = $1
                  and not exists (
                    select 1 from tax_raids
                    where status = 'success' and raid_until > now()
                  )
                """,
                tg_id, int(amount),
            )
    except Exception as e:
        log.warning("tax accrue failed for tg=%s amount=%s kind=%s: %s",
                    tg_id, amount, kind, e)


# ============================================================
# HOURLY TICK
# ============================================================

async def hourly_tick_user(tg_id: int) -> dict:
    """Convert this player's accumulated income into pending_tax_due.
    Idempotent — won't double-charge if called twice in the same hour.
    Returns a small report (charged, audited, black_books_proc, etc.)."""
    now = datetime.now(timezone.utc)
    today = now.date()
    report = {"charged": 0, "exempted": 0, "income": 0,
              "black_books_proc": False, "audited": False, "audit_penalty": 0,
              "audit_cashback": 0}

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from tax_users where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return report

            # Skip if last tick was within the last hour (prevent double-charge
            # if two ticks fire close together).
            if (row["last_tick_at"] is not None and
                (now - row["last_tick_at"]).total_seconds() < HOURLY_TICK_SECONDS - 60):
                return report

            income = int(row["pending_taxable_income"])
            upgrades = _parse_jsonb(row["upgrades"]) or {}
            entity_level = int(row["entity_level"])
            paradise_until = row["paradise_until"]
            paradise_active = paradise_until is not None and paradise_until > now
            declared_today = (row["last_declared_at"] is not None and
                              row["last_declared_at"].date() == today)
            honest = bool(row["honest_citizen"])

            # Newbie exemption: lifetime earnings under threshold → 0% tax.
            # Just clear the income accumulator and bail.
            eu = await conn.fetchrow(
                "select total_earned from economy_users where tg_id = $1", tg_id,
            )
            total_earned = int(eu["total_earned"]) if eu else 0
            if total_earned < NEWBIE_EXEMPTION_THRESHOLD:
                await conn.execute(
                    "update tax_users set pending_taxable_income = 0, last_tick_at = $2 "
                    "where tg_id = $1", tg_id, now,
                )
                return report

            rate = _effective_rate(entity_level, upgrades, declared_today, honest, paradise_active)
            exemption = _income_exemption(upgrades)
            taxable = max(0, income - exemption)
            tax_owed = int(taxable * rate)

            # Чёрная бухгалтерия — chance to wipe this hour's tax
            if random.random() < _black_books_chance(upgrades):
                tax_owed = 0
                report["black_books_proc"] = True

            # Random audit
            if random.random() < AUDIT_CHANCE_PER_TICK:
                report["audited"] = True
                if int(row["tax_debt"]) > 0:
                    # Penalty: tax_debt × 2 — accumulates further
                    penalty = int(row["tax_debt"])
                    await conn.execute(
                        "update tax_users set tax_debt = tax_debt + $2 where tg_id = $1",
                        tg_id, penalty,
                    )
                    report["audit_penalty"] = penalty
                else:
                    # Clean record — 5% cashback of total taxes paid (one-shot reward,
                    # capped at 100M to avoid abuse).
                    cashback = min(100_000_000, int(int(row["total_taxes_paid"]) * 0.05))
                    if cashback > 0:
                        await conn.execute(
                            "update economy_users set balance = balance + $2 where tg_id = $1",
                            tg_id, cashback,
                        )
                        report["audit_cashback"] = cashback

            await conn.execute(
                """
                update tax_users set
                  pending_taxable_income = 0,
                  pending_tax_due = pending_tax_due + $2,
                  last_tick_at = $3,
                  total_audits = total_audits + $4
                where tg_id = $1
                """,
                tg_id, tax_owed, now, 1 if report["audited"] else 0,
            )
            report["charged"] = tax_owed
            report["exempted"] = min(income, exemption)
            report["income"] = income

    return report


async def hourly_tick_all() -> None:
    """Run hourly_tick_user for every player who has either pending income or
    accrued tax — small fan-out, fine to do serially."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select tg_id from tax_users "
            "where pending_taxable_income > 0 or pending_tax_due > 0 or tax_debt > 0"
        )
    for r in rows:
        try:
            await hourly_tick_user(int(r["tg_id"]))
        except Exception:
            log.exception("tax hourly tick failed for tg=%s", r["tg_id"])


# ============================================================
# MIDNIGHT SET (Forced collection) + debt compounding
# ============================================================

async def midnight_collect_user(tg_id: int) -> dict:
    """Forced-collection at midnight UTC. Simple: deduct pending_tax_due from
    balance. Balance is allowed to go negative — no debt tracking, no penalty,
    no blocking. Player just has to climb back to 0+ via earnings to play
    paid actions again (game bets check balance >= cost, so negative balance
    blocks paid bets naturally)."""
    now = datetime.now(timezone.utc)
    today = now.date()
    report = {"collected": 0, "new_balance": None,
              "honest_citizen_unlocked": False, "punctual_cashback": 0}

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from tax_users where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return report

            upgrades = _parse_jsonb(row["upgrades"]) or {}
            pending = int(row["pending_tax_due"])

            # Deduct unconditionally — let balance go negative if needed.
            if pending > 0:
                bal_row = await conn.fetchrow(
                    "update economy_users set balance = balance - $2, "
                    "total_spent = total_spent + $2 "
                    "where tg_id = $1 returning balance",
                    tg_id, pending,
                )
                new_bal = int(bal_row["balance"]) if bal_row else 0
                report["collected"] = pending
                report["new_balance"] = new_bal
                try:
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "values ($1, $2, 'tax_set', 'midnight_set_collection', $3)",
                        tg_id, -pending, new_bal,
                    )
                except Exception:
                    pass

            # Streak: every day is a "clean day" now (no debt concept). Just
            # advance counter once per UTC day.
            new_streak = int(row["streak_punctual_days"])
            last_check = row["last_streak_check"]
            if last_check is None or last_check < today:
                new_streak += 1

            new_honest = bool(row["honest_citizen"])
            if new_streak >= HONEST_CITIZEN_DAYS and not new_honest:
                new_honest = True
                report["honest_citizen_unlocked"] = True

            # Кэшбэк за пунктуальность — every 7 clean days, perk owners get a small bonus.
            cashback_lvl = int(upgrades.get("punctual_cashback", 0))
            if cashback_lvl >= 1 and new_streak > 0 and new_streak % 7 == 0:
                cb = int(report["collected"] * 0.7)
                if cb > 0:
                    await conn.execute(
                        "update economy_users set balance = balance + $2 where tg_id = $1",
                        tg_id, cb,
                    )
                    report["punctual_cashback"] = cb

            await conn.execute(
                """
                update tax_users set
                  pending_tax_due = 0,
                  tax_debt = 0,
                  debt_since = NULL,
                  total_taxes_paid = total_taxes_paid + $2,
                  last_collected_at = $3,
                  streak_punctual_days = $4,
                  last_streak_check = $5,
                  honest_citizen = $6
                where tg_id = $1
                """,
                tg_id, report["collected"], now, new_streak, today, new_honest,
            )
    return report


async def midnight_collect_all() -> None:
    """Run for every player with pending tax or existing debt."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select tg_id from tax_users "
            "where pending_tax_due > 0 or tax_debt > 0"
        )
    for r in rows:
        try:
            await midnight_collect_user(int(r["tg_id"]))
        except Exception:
            log.exception("tax midnight collect failed for tg=%s", r["tg_id"])


# ============================================================
# PLAYER ACTIONS
# ============================================================

async def pay_tax(tg_id: int, amount: int | None = None) -> dict:
    """Manually pay all (or partial) pending_tax_due + tax_debt. Drains in
    that order — pending first, then debt."""
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select pending_tax_due, tax_debt from tax_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            pending = int(row["pending_tax_due"])
            debt    = int(row["tax_debt"])
            total_owed = pending + debt
            if total_owed <= 0:
                return {"ok": False, "error": "Налогов к оплате нет"}

            pay = total_owed if amount is None else min(int(amount), total_owed)

            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", tg_id,
            )
            bal = int(bal_row["balance"]) if bal_row else 0
            if bal < pay:
                return {"ok": False, "error": "Не хватает монет", "have": bal, "need": pay}

            await conn.execute(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1", tg_id, pay,
            )
            new_bal = bal - pay

            # Drain pending first, debt after.
            pay_pending = min(pay, pending)
            pay_debt    = pay - pay_pending
            new_pending = pending - pay_pending
            new_debt    = debt - pay_debt
            debt_since  = None if new_debt <= 0 else "keep"

            if debt_since == "keep":
                await conn.execute(
                    "update tax_users set pending_tax_due = $2, tax_debt = $3, "
                    "total_taxes_paid = total_taxes_paid + $4 where tg_id = $1",
                    tg_id, new_pending, new_debt, pay,
                )
            else:
                await conn.execute(
                    "update tax_users set pending_tax_due = $2, tax_debt = $3, debt_since = NULL, "
                    "total_taxes_paid = total_taxes_paid + $4 where tg_id = $1",
                    tg_id, new_pending, new_debt, pay,
                )
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'tax_pay', 'manual_pay', $3)",
                    tg_id, -pay, new_bal,
                )
            except Exception:
                pass
    return {"ok": True, "paid": pay, "new_balance": new_bal,
            "pending_left": new_pending, "debt_left": new_debt}


async def register_entity(tg_id: int, target_level: int) -> dict:
    """Pay registration fee to switch to a higher entity tier."""
    target_level = int(target_level)
    target = ENTITY_BY_LEVEL.get(target_level)
    if target is None:
        return {"ok": False, "error": "Unknown entity"}

    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select entity_level from tax_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            cur_level = int(row["entity_level"])
            if target_level <= cur_level:
                return {"ok": False, "error": "Уже зарегистрирован на этом или выше уровне"}

            fee = int(target["reg_fee"])
            if fee > 0:
                bal_row = await conn.fetchrow(
                    "select balance from economy_users where tg_id = $1 for update", tg_id,
                )
                bal = int(bal_row["balance"]) if bal_row else 0
                if bal < fee:
                    return {"ok": False, "error": "Не хватает монет на регистрацию",
                            "need": fee, "have": bal}
                await conn.execute(
                    "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                    "where tg_id = $1", tg_id, fee,
                )
                try:
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "values ($1, $2, 'tax_register', $3, (select balance from economy_users where tg_id = $1))",
                        tg_id, -fee, f"register_{target['key']}",
                    )
                except Exception:
                    pass
            await conn.execute(
                "update tax_users set entity_level = $2 where tg_id = $1",
                tg_id, target_level,
            )
    return {"ok": True, "new_entity": target_level, "name": target["name"], "spent": fee}


async def buy_upgrade(tg_id: int, key: str) -> dict:
    if key not in UPGRADE_DEFS:
        return {"ok": False, "error": "Unknown upgrade"}
    cfg = UPGRADE_DEFS[key]
    tiers = cfg["tiers"]
    max_lvl = len(tiers)
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select upgrades, paradise_until, last_paradise_at "
                "from tax_users where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            ups = _parse_jsonb(row["upgrades"]) or {}
            cur = int(ups.get(key, 0))
            if cur >= max_lvl:
                return {"ok": False, "error": "Максимум уровень"}
            _, _, cost = tiers[cur]
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", tg_id,
            )
            bal = int(bal_row["balance"]) if bal_row else 0
            if bal < cost:
                return {"ok": False, "error": "Не хватает монет", "cost": cost}

            # Special handling: 'tax_paradise' is a one-shot consumable that
            # activates the 7-day buff. Limited to once per real month.
            if key == "tax_paradise":
                last_paradise = row["last_paradise_at"]
                now = datetime.now(timezone.utc)
                if last_paradise is not None and (now - last_paradise).total_seconds() < 30 * 24 * 3600:
                    return {"ok": False, "error": "Налоговый рай — 1 раз в месяц",
                            "next_at": (last_paradise + timedelta(days=30)).isoformat()}
                paradise_until = now + timedelta(days=7)
                await conn.execute(
                    "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                    "where tg_id = $1", tg_id, cost,
                )
                await conn.execute(
                    "update tax_users set paradise_until = $2, last_paradise_at = $3 "
                    "where tg_id = $1",
                    tg_id, paradise_until, now,
                )
                return {"ok": True, "kind": "paradise_activated",
                        "paradise_until": paradise_until.isoformat(),
                        "spent": cost, "new_balance": bal - cost}

            # Standard upgrades
            ups[key] = cur + 1
            await conn.execute(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1", tg_id, cost,
            )
            await conn.execute(
                "update tax_users set upgrades = $2::jsonb where tg_id = $1",
                tg_id, json.dumps(ups),
            )
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'tax_upgrade', $3, (select balance from economy_users where tg_id = $1))",
                    tg_id, -cost, f"upgrade_{key}_lvl{cur+1}",
                )
            except Exception:
                pass
    return {"ok": True, "key": key, "new_level": cur + 1, "spent": cost,
            "new_balance": bal - cost}


async def declare(tg_id: int) -> dict:
    """Daily declaration — gives -1% tax for the next day. One per real day."""
    await ensure_user(tg_id)
    now = datetime.now(timezone.utc)
    today = now.date()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select last_declared_at from tax_users where tg_id = $1 for update", tg_id,
            )
            if row and row["last_declared_at"] and row["last_declared_at"].date() == today:
                return {"ok": False, "error": "Уже подавал декларацию сегодня"}
            await conn.execute(
                "update tax_users set last_declared_at = $2 where tg_id = $1",
                tg_id, now,
            )
    return {"ok": True, "declared_at": now.isoformat()}


async def amnesty(tg_id: int) -> dict:
    """Wipe full debt for 50% of debt amount. Once per real month (30 days)."""
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select tax_debt, last_amnesty_at from tax_users where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            debt = int(row["tax_debt"])
            if debt <= 0:
                return {"ok": False, "error": "Долгов нет"}
            now = datetime.now(timezone.utc)
            last = row["last_amnesty_at"]
            if last is not None and (now - last).total_seconds() < 30 * 24 * 3600:
                return {"ok": False, "error": "Амнистия — 1 раз в месяц",
                        "next_at": (last + timedelta(days=30)).isoformat()}
            cost = max(1, debt // 2)
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", tg_id,
            )
            bal = int(bal_row["balance"]) if bal_row else 0
            if bal < cost:
                return {"ok": False, "error": "Не хватает монет", "need": cost, "have": bal}
            await conn.execute(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1", tg_id, cost,
            )
            await conn.execute(
                "update tax_users set tax_debt = 0, debt_since = NULL, "
                "last_amnesty_at = $2, total_amnesties = total_amnesties + 1 "
                "where tg_id = $1", tg_id, now,
            )
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'tax_amnesty', $3, (select balance from economy_users where tg_id = $1))",
                    tg_id, -cost, f"amnesty_{debt}",
                )
            except Exception:
                pass
    return {"ok": True, "wiped_debt": debt, "paid": cost, "new_balance": bal - cost}


# ============================================================
# DEBT GUARD — used by other modules to block actions while indebted
# ============================================================

async def has_debt(tg_id: int) -> bool:
    async with pool().acquire() as conn:
        v = await conn.fetchval(
            "select tax_debt from tax_users where tg_id = $1", tg_id,
        )
    return v is not None and int(v) > 0


# ============================================================
# RAID ON THE TAX OFFICE (coop event)
# ============================================================
# A player initiates a raid → 10-min prep window → other players donate skins
# from inventory → if 500+ skins donated by deadline, raid succeeds → tax
# office is offline for 2 hours server-wide (no `accrue_tax` calls take effect).
# 1 raid per 24h cap. Donated skins are CONSUMED (always, win or lose).

async def is_tax_office_open() -> bool:
    """True if no successful raid is currently shutting down the office."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select raid_until from tax_raids "
            "where status = 'success' and raid_until > now() "
            "order by raid_until desc limit 1"
        )
    return row is None


async def get_active_raid() -> dict | None:
    """Return the current preparing raid, OR an active successful raid (within
    its 2h window). Used by UI to show raid banner/progress."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            select * from tax_raids
            where status = 'preparing'
               or (status = 'success' and raid_until > now())
            order by id desc limit 1
            """
        )
        if row is None:
            return None
        # Pull donations for display
        donations = await conn.fetch(
            """
            select d.user_id, d.skins, d.donated_at,
                   u.username, u.first_name, u.photo_url
            from tax_raid_donations d
            left join users u on u.tg_id = d.user_id
            where d.raid_id = $1
            order by d.skins desc, d.donated_at asc
            limit 50
            """, int(row["id"]),
        )
    return {
        "id":             int(row["id"]),
        "initiator_id":   int(row["initiator_id"]) if row["initiator_id"] else None,
        "status":         row["status"],
        "started_at":     row["started_at"].isoformat() if row["started_at"] else None,
        "deadline":       row["deadline"].isoformat() if row["deadline"] else None,
        "succeeded_at":   row["succeeded_at"].isoformat() if row["succeeded_at"] else None,
        "raid_until":     row["raid_until"].isoformat() if row["raid_until"] else None,
        "skins_donated":  int(row["skins_donated"]),
        "skins_required": int(row["skins_required"]),
        "donations": [
            {
                "user_id":    int(d["user_id"]),
                "skins":      int(d["skins"]),
                "username":   d["username"],
                "first_name": d["first_name"],
                "photo_url":  d["photo_url"],
                "donated_at": d["donated_at"].isoformat() if d["donated_at"] else None,
            } for d in donations
        ],
    }


async def start_raid(tg_id: int) -> dict:
    """Initiate a raid. Server-wide cooldown of RAID_COOLDOWN_HOURS since the
    last raid started, regardless of outcome. Only one raid can be active."""
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        async with conn.transaction():
            # Block if any raid is preparing OR successful-and-active
            active = await conn.fetchrow(
                "select id, status from tax_raids "
                "where status = 'preparing' or "
                "      (status = 'success' and raid_until > now()) "
                "order by id desc limit 1"
            )
            if active is not None:
                return {"ok": False, "error": "Рейд уже идёт"}

            # Cooldown — 24h since the last raid's started_at
            last = await conn.fetchrow(
                "select started_at from tax_raids order by id desc limit 1"
            )
            if last is not None and last["started_at"] is not None:
                elapsed = (now - last["started_at"]).total_seconds()
                if elapsed < RAID_COOLDOWN_HOURS * 3600:
                    next_at = last["started_at"] + timedelta(hours=RAID_COOLDOWN_HOURS)
                    return {
                        "ok": False,
                        "error": f"Рейд можно созвать раз в {RAID_COOLDOWN_HOURS}ч",
                        "next_at": next_at.isoformat(),
                    }

            deadline = now + timedelta(seconds=RAID_PREP_SECONDS)
            row = await conn.fetchrow(
                """
                insert into tax_raids
                  (initiator_id, status, started_at, deadline, skins_required)
                values ($1, 'preparing', $2, $3, $4)
                returning id
                """,
                tg_id, now, deadline, RAID_REQUIRED_SKINS,
            )
    log.info("tax raid #%s started by tg=%s, deadline=%s", row["id"], tg_id, deadline)
    return {"ok": True, "raid_id": int(row["id"]), "deadline": deadline.isoformat()}


async def donate_skins_to_raid(tg_id: int, raid_id: int, count: int) -> dict:
    """Take `count` skins from player's inventory and donate to the active
    preparing raid. Skins are CONSUMED — deleted from inventory regardless
    of raid outcome (they're "ammunition" for the attack)."""
    count = max(RAID_MIN_DONATION, min(int(count or 0), RAID_MAX_DONATION_PER_TX))
    async with pool().acquire() as conn:
        async with conn.transaction():
            raid = await conn.fetchrow(
                "select * from tax_raids where id = $1 for update", raid_id,
            )
            if raid is None:
                return {"ok": False, "error": "Рейд не найден"}
            if raid["status"] != "preparing":
                return {"ok": False, "error": "Рейд уже не принимает пожертвования"}
            now = datetime.now(timezone.utc)
            if raid["deadline"] is not None and raid["deadline"] <= now:
                return {"ok": False, "error": "Время сборов истекло"}

            already = int(raid["skins_donated"])
            need = int(raid["skins_required"]) - already
            if need <= 0:
                return {"ok": False, "error": "Уже собрано достаточно скинов"}
            count = min(count, need)

            # Find `count` skins in inventory (any non-locked items)
            ids = await conn.fetch(
                """
                select id from economy_inventory
                where user_id = $1
                  and (jackpot_round_id is null)
                limit $2
                for update
                """, tg_id, count,
            )
            if len(ids) < count:
                return {
                    "ok": False,
                    "error": f"Не хватает скинов в инвентаре ({len(ids)}/{count})",
                    "have": len(ids),
                }
            skin_ids = [int(r["id"]) for r in ids]
            await conn.execute(
                "delete from economy_inventory where id = any($1::bigint[])", skin_ids,
            )

            # Update raid + donations
            new_total = already + count
            await conn.execute(
                "update tax_raids set skins_donated = $2 where id = $1",
                raid_id, new_total,
            )
            await conn.execute(
                """
                insert into tax_raid_donations (raid_id, user_id, skins, donated_at)
                values ($1, $2, $3, now())
                on conflict (raid_id, user_id) do update
                set skins = tax_raid_donations.skins + excluded.skins,
                    donated_at = now()
                """,
                raid_id, tg_id, count,
            )

            # Auto-finalize if quota reached
            if new_total >= int(raid["skins_required"]):
                await _finalize_raid_locked(conn, raid_id)

    return {"ok": True, "donated": count, "raid_total": new_total,
            "required": int(raid["skins_required"])}


async def _finalize_raid_locked(conn, raid_id: int) -> dict:
    """Resolve a raid (called inside a held transaction). Marks success or
    failed based on whether quota was hit. On success, sets raid_until to
    now + 2h."""
    now = datetime.now(timezone.utc)
    raid = await conn.fetchrow(
        "select status, skins_donated, skins_required from tax_raids "
        "where id = $1 for update", raid_id,
    )
    if raid is None or raid["status"] != "preparing":
        return {"ok": False, "already_done": True}
    if int(raid["skins_donated"]) >= int(raid["skins_required"]):
        raid_until = now + timedelta(seconds=RAID_SUCCESS_DURATION_SEC)
        await conn.execute(
            "update tax_raids set status = 'success', succeeded_at = $2, raid_until = $3 "
            "where id = $1", raid_id, now, raid_until,
        )
        log.info("tax raid #%s SUCCESS — tax office offline until %s", raid_id, raid_until)
        return {"ok": True, "outcome": "success", "raid_until": raid_until.isoformat()}
    else:
        await conn.execute(
            "update tax_raids set status = 'failed' where id = $1", raid_id,
        )
        log.info("tax raid #%s FAILED — only %s/%s skins", raid_id,
                 raid["skins_donated"], raid["skins_required"])
        return {"ok": True, "outcome": "failed",
                "donated": int(raid["skins_donated"]),
                "required": int(raid["skins_required"])}


async def finalize_expired_raids() -> None:
    """Background sweeper — finds preparing raids past deadline and resolves
    them. Also flips successful raids whose 2h window expired to 'expired'
    (cosmetic, doesn't affect tax behavior since we already check raid_until)."""
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        # Resolve expired prep raids
        expired = await conn.fetch(
            "select id from tax_raids where status = 'preparing' and deadline <= $1",
            now,
        )
        for r in expired:
            try:
                async with conn.transaction():
                    await _finalize_raid_locked(conn, int(r["id"]))
            except Exception:
                log.exception("finalize_raid failed for %s", r["id"])
        # Mark old successes as expired (housekeeping)
        await conn.execute(
            "update tax_raids set status = 'expired' "
            "where status = 'success' and raid_until <= $1",
            now,
        )


# ============================================================
# DEBT GUARD — used by other modules to block actions while indebted
# ============================================================

async def has_debt(tg_id: int) -> bool:
    async with pool().acquire() as conn:
        v = await conn.fetchval(
            "select tax_debt from tax_users where tg_id = $1", tg_id,
        )
    return v is not None and int(v) > 0


# ============================================================
# BACKGROUND LOOPS
# ============================================================

async def hourly_loop() -> None:
    import asyncio
    # Tick every 5 minutes — hourly_tick_user is idempotent within the hour,
    # so frequent calls just check the gate. This lets us catch newly-online
    # players quickly without waiting a full hour from server start.
    while True:
        try:
            await asyncio.sleep(300)
            await hourly_tick_all()
        except Exception:
            log.exception("tax hourly_loop tick failed")


async def raid_loop() -> None:
    """Periodic sweeper for raid lifecycle — resolves expired prep windows
    and flips done success windows to 'expired'. Runs every 20 seconds."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(20)
            await finalize_expired_raids()
        except Exception:
            log.exception("tax raid_loop tick failed")


async def midnight_loop() -> None:
    """Fire SET collection once a day at 00:00 UTC."""
    import asyncio
    while True:
        try:
            now = datetime.now(timezone.utc)
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
            wait = (tomorrow - now).total_seconds()
            await asyncio.sleep(max(60, wait))
            log.info("tax: midnight SET collection starting")
            await midnight_collect_all()
            log.info("tax: midnight SET done")
        except Exception:
            log.exception("tax midnight_loop tick failed")
