"""Tax Authority (Налоговая) — daily income tax at 00:00 UTC.

Players register a legal entity (Физ.лицо / Самозанятый / ИП / ООО / Холдинг /
Офшор) which sets their base tax rate. They can stack perks that lower the
rate, raise the daily income exemption, and unlock special actions
(декларация, налоговый рай, рейд на налоговую).

Earnings flow:
1. Every credit to balance from a taxable source calls `accrue_tax(tg_id, amt, kind)`
   which appends to `pending_taxable_income`.
2. At 00:00 UTC each day, `daily_tick_user(tg_id)` runs:
   - Compute tax = (income − exemption) × effective_rate
   - Roll black-books / random audit
   - Deduct directly from balance (allowed to go NEGATIVE)
   - Zero the income accumulator, advance streak
3. Players can pay manually anytime (`pay_tax`) — same logic, just on demand.

Creative mechanics:
- 🕵 Random audit (15% per daily tick): clean record → 5% cashback from total
  taxes paid (capped at 100M).
- 📋 Daily declaration: −1% rate for the day.
- 🏖 Налоговый рай: 7 days of 0% tax (1× / real month).
- 🏆 Honest Citizen: 30 clean days streak → permanent −1% rate badge.
- 🎯 RAID ON THE TAX OFFICE: coop event — 500 skins donated within 10 min prep
  shuts down accrue_tax for 25h server-wide (one full daily cycle).
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
        "desc": "Первые X 🪙/день не облагаются", "unit": "🪙/день",
        # Bumped 24× since the window is now daily not hourly:
        # 2.4M at lvl 1 → ~1.2B at lvl 10
        "tiers": _build_tiers(10, lambda L: int(2_400_000 * (1.85 ** (L - 1))),
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
        "desc": "Шанс что налог за день «сольётся»", "unit": "%",
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
# (Kept for legacy; debt system was removed. Still referenced by old UI fields.)
BASE_DEBT_PENALTY_PER_DAY = 0.10

# Daily tick — tax is computed and collected ONCE per day at 00:00 UTC. The
# midnight_loop fires daily_collect_all; a maintenance loop also catches
# stragglers (players whose last collection is more than 23h old) every 30 min.
DAILY_TICK_SECONDS = 24 * 3600

# Random-audit chance per daily tick (15% — was 1% × ~24 ticks/day previously).
AUDIT_CHANCE_PER_TICK = 0.15

# Honest Citizen: how many consecutive clean days unlock the permanent badge.
HONEST_CITIZEN_DAYS = 30

# Newbie exemption: players whose lifetime `economy_users.total_earned` is below
# this threshold pay zero tax. Lets new players ramp up without a punishing
# tax drag in their first sessions.
NEWBIE_EXEMPTION_THRESHOLD = 1_000_000_000  # 1B lifetime earnings

# RAIDS — coop event to shut down the tax office.
# Tuned for the daily-tick world: 25h success window guarantees exactly one
# daily tax cycle gets skipped. Cooldown 48h prevents perpetual evasion.
RAID_PREP_SECONDS         = 10 * 60         # 10 minutes preparation window
RAID_SUCCESS_DURATION_SEC = 25 * 60 * 60    # 25 hours — covers one full daily tick
RAID_REQUIRED_SKINS       = 500             # total donations needed
RAID_COOLDOWN_HOURS       = 48              # 1 raid per 48h server-wide
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
    """Налоговый вычет — first X 🪙/day are exempted from taxation."""
    lvl = int((upgrades or {}).get("tax_deduction", 0))
    if lvl <= 0:
        return 0
    return int(2_400_000 * (1.85 ** (lvl - 1)))


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

    # Estimate of how much tax will be charged at the NEXT daily tick (00:00 UTC).
    # UI shows: "К списанию в полночь UTC: X 🪙".
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
        "income_exemption_per_day": _income_exemption(upgrades),
        # Backward-compat alias for older clients
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
    raw = float(entity["rate"])
    out = [{"label": f"{entity['icon']} {entity['name']}", "value": f"{entity['rate']*100:.0f}%"}]
    accountant = int((upgrades or {}).get("accountant", 0))
    if accountant > 0:
        out.append({"label": "📒 Бухгалтер", "value": f"−{accountant * 0.5:.1f}%"})
        raw -= accountant * 0.005
    if int((upgrades or {}).get("small_biz_relief", 0)) >= 1 and entity_level in (2, 3):
        out.append({"label": "🏪 Льготы малого бизнеса", "value": "−2%"})
        raw -= 0.02
    if int((upgrades or {}).get("offshore_links", 0)) >= 1 and entity_level in (4, 5):
        out.append({"label": "🌐 Оффшорные связи", "value": "−3%"})
        raw -= 0.03
    if declared_today:
        out.append({"label": "📋 Декларация подана", "value": "−1%"})
        raw -= 0.01
    if honest_citizen:
        out.append({"label": "🏆 Honest Citizen (30 дней без долгов)", "value": "−1%"})
        raw -= 0.01
    # Floor: 1% по закону. Если математика уходит ниже — показываем явно
    # сколько прибавляется обратно, чтобы игрок видел, что налог не уходит в минус.
    if raw < 0.01:
        gap = 0.01 - raw
        out.append({
            "label": "🛡 Минимум по закону",
            "value": f"+{gap*100:.1f}%",
            "color": "#ffd700",
        })
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

async def daily_tick_user(tg_id: int) -> dict:
    """Single combined operation: compute tax on the day's income and DEDUCT
    DIRECTLY from balance (allowed to go negative). Replaces the old
    hourly_tick + midnight_collect two-phase flow.

    Idempotent within DAILY_TICK_SECONDS — calling twice within ~23h is a no-op.
    Audit/black-books/streak/honest-citizen all roll once per daily tick."""
    now = datetime.now(timezone.utc)
    today = now.date()
    report = {"charged": 0, "exempted": 0, "income": 0,
              "black_books_proc": False, "audited": False, "audit_cashback": 0,
              "honest_citizen_unlocked": False, "punctual_cashback": 0,
              "new_balance": None}

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from tax_users where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return report

            # Skip if we already collected within the last 23h — guard against
            # midnight + maintenance loops both firing on the same player.
            if (row["last_collected_at"] is not None and
                (now - row["last_collected_at"]).total_seconds() < DAILY_TICK_SECONDS - 3600):
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
            eu = await conn.fetchrow(
                "select total_earned from economy_users where tg_id = $1", tg_id,
            )
            total_earned = int(eu["total_earned"]) if eu else 0
            if total_earned < NEWBIE_EXEMPTION_THRESHOLD:
                # Newbie — clear income accumulator, advance streak, bail.
                new_streak_newbie = _streak_advance(row, today)
                await conn.execute(
                    """
                    update tax_users set
                      pending_taxable_income = 0,
                      last_tick_at = $2,
                      last_collected_at = $2,
                      streak_punctual_days = $3,
                      last_streak_check = $4
                    where tg_id = $1
                    """,
                    tg_id, now, new_streak_newbie, today,
                )
                return report

            rate = _effective_rate(entity_level, upgrades, declared_today, honest, paradise_active)
            exemption = _income_exemption(upgrades)
            taxable = max(0, income - exemption)
            tax_owed = int(taxable * rate)

            # Чёрная бухгалтерия — chance to wipe this day's tax entirely.
            if tax_owed > 0 and random.random() < _black_books_chance(upgrades):
                tax_owed = 0
                report["black_books_proc"] = True

            # Random audit (clean-only cashback now — debt system was removed)
            if random.random() < AUDIT_CHANCE_PER_TICK:
                report["audited"] = True
                cashback = min(100_000_000, int(int(row["total_taxes_paid"]) * 0.05))
                if cashback > 0:
                    await conn.execute(
                        "update economy_users set balance = balance + $2 where tg_id = $1",
                        tg_id, cashback,
                    )
                    report["audit_cashback"] = cashback

            # Deduct tax directly from balance — allow negative.
            new_bal = None
            if tax_owed > 0:
                bal_row = await conn.fetchrow(
                    "update economy_users set balance = balance - $2, "
                    "total_spent = total_spent + $2 "
                    "where tg_id = $1 returning balance",
                    tg_id, tax_owed,
                )
                new_bal = int(bal_row["balance"]) if bal_row else 0
                report["new_balance"] = new_bal
                try:
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "values ($1, $2, 'tax_set', 'daily_set_collection', $3)",
                        tg_id, -tax_owed, new_bal,
                    )
                except Exception:
                    pass

            # Streak: every clean day (no debt) bumps. With debt removed, every
            # day is "clean" by definition — counter just counts daily ticks.
            new_streak = int(row["streak_punctual_days"])
            last_check = row["last_streak_check"]
            if last_check is None or last_check < today:
                new_streak += 1

            new_honest = bool(row["honest_citizen"])
            if new_streak >= HONEST_CITIZEN_DAYS and not new_honest:
                new_honest = True
                report["honest_citizen_unlocked"] = True

            # Кэшбэк за пунктуальность — every 7 clean days, perk owners get
            # a small bonus equal to ~70% of today's tax.
            cashback_lvl = int(upgrades.get("punctual_cashback", 0))
            if cashback_lvl >= 1 and new_streak > 0 and new_streak % 7 == 0:
                cb = int(tax_owed * 0.7)
                if cb > 0:
                    await conn.execute(
                        "update economy_users set balance = balance + $2 where tg_id = $1",
                        tg_id, cb,
                    )
                    report["punctual_cashback"] = cb

            await conn.execute(
                """
                update tax_users set
                  pending_taxable_income = 0,
                  pending_tax_due = 0,
                  tax_debt = 0,
                  debt_since = NULL,
                  last_tick_at = $2,
                  last_collected_at = $2,
                  total_taxes_paid = total_taxes_paid + $3,
                  total_audits = total_audits + $4,
                  streak_punctual_days = $5,
                  last_streak_check = $6,
                  honest_citizen = $7
                where tg_id = $1
                """,
                tg_id, now, tax_owed,
                1 if report["audited"] else 0,
                new_streak, today, new_honest,
            )
            report["charged"] = tax_owed
            report["exempted"] = min(income, exemption)
            report["income"] = income

    return report


def _streak_advance(row, today) -> int:
    """Helper: bump streak counter once per UTC day (idempotent)."""
    cur = int(row["streak_punctual_days"])
    last_check = row["last_streak_check"]
    if last_check is None or last_check < today:
        return cur + 1
    return cur


async def daily_tick_all() -> None:
    """Sweep every player whose accumulated income or last_collected_at warrants
    a daily tick. Cheap query, idempotent per-user."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select tg_id from tax_users "
            "where pending_taxable_income > 0 "
            "   or last_collected_at is null "
            "   or last_collected_at < now() - interval '23 hours'"
        )
    for r in rows:
        try:
            await daily_tick_user(int(r["tg_id"]))
        except Exception:
            log.exception("tax daily tick failed for tg=%s", r["tg_id"])


# Backward-compat aliases — older code paths may still reference these.
hourly_tick_user = daily_tick_user
hourly_tick_all  = daily_tick_all


# ============================================================
# MIDNIGHT SET — single combined daily collection (alias to daily_tick_*)
# ============================================================

async def midnight_collect_user(tg_id: int) -> dict:
    """Backward-compat alias: in the daily-tick world, midnight collection IS
    the daily tick — single combined operation."""
    return await daily_tick_user(tg_id)


async def midnight_collect_all() -> None:
    """Backward-compat: the daily 00:00 UTC sweep delegated to daily_tick_all."""
    await daily_tick_all()


# ============================================================
# PLAYER ACTIONS
# ============================================================

async def pay_tax(tg_id: int, amount: int | None = None) -> dict:
    """Manually pay the day's tax NOW (instead of waiting for the daily SET).
    Computes tax on-the-fly from pending_taxable_income × current effective
    rate, applying exemption / paradise / newbie checks identical to the
    automatic daily tick. Balance allowed to go negative."""
    await ensure_user(tg_id)
    now = datetime.now(timezone.utc)
    today = now.date()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from tax_users where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}

            income = int(row["pending_taxable_income"])
            if income <= 0:
                return {"ok": False, "error": "Налогов к оплате нет"}

            upgrades = _parse_jsonb(row["upgrades"]) or {}
            entity_level = int(row["entity_level"])
            paradise_until = row["paradise_until"]
            paradise_active = paradise_until is not None and paradise_until > now
            declared_today = (row["last_declared_at"] is not None and
                              row["last_declared_at"].date() == today)
            honest = bool(row["honest_citizen"])

            # Newbie? No tax owed.
            eu = await conn.fetchrow(
                "select total_earned from economy_users where tg_id = $1", tg_id,
            )
            total_earned = int(eu["total_earned"]) if eu else 0
            if total_earned < NEWBIE_EXEMPTION_THRESHOLD:
                await conn.execute(
                    "update tax_users set pending_taxable_income = 0 where tg_id = $1",
                    tg_id,
                )
                return {"ok": True, "paid": 0, "new_balance": None,
                        "note": "Налогов нет — вы в каникулах"}

            rate = _effective_rate(entity_level, upgrades, declared_today, honest, paradise_active)
            exemption = _income_exemption(upgrades)
            taxable = max(0, income - exemption)
            tax_owed = int(taxable * rate)
            if tax_owed <= 0:
                await conn.execute(
                    "update tax_users set pending_taxable_income = 0 where tg_id = $1",
                    tg_id,
                )
                return {"ok": True, "paid": 0, "new_balance": None,
                        "note": "Доход полностью покрыт вычетом"}

            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1 returning balance",
                tg_id, tax_owed,
            )
            new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
            await conn.execute(
                """
                update tax_users set
                  pending_taxable_income = 0,
                  pending_tax_due = 0,
                  total_taxes_paid = total_taxes_paid + $2,
                  last_collected_at = $3
                where tg_id = $1
                """,
                tg_id, tax_owed, now,
            )
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'tax_pay', 'manual_pay', $3)",
                    tg_id, -tax_owed, new_bal,
                )
            except Exception:
                pass
    return {"ok": True, "paid": tax_owed, "new_balance": new_bal}


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
    """Maintenance sweep — runs every 30 minutes. The daily tick is idempotent
    within ~23h, so this catches stragglers (e.g. server restarts during the
    midnight window). Cheap when there's nothing to do."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(1800)
            await daily_tick_all()
        except Exception:
            log.exception("tax maintenance loop tick failed")


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
