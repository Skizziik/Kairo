"""Casino tier/rank system based on lifetime wager (gross bet volume).

30 tiers from Bronze (0) to Godlike (1 quadrillion). Each casino bet
increments `economy_users.lifetime_wager` regardless of win/loss
(audit.log_bet handles the bump). The tier is a pure function of that
number — no separate state to keep in sync.

The badge is shown on the home profile card and next to the player's
name on leaderboards.
"""
from __future__ import annotations

from pathlib import Path

from app.db.client import pool


# ============================================================
# TIER TABLE (30 tiers, ratios slow toward the top so Godlike is mythic)
# ============================================================

# Order matters — lookup walks bottom-up. Each tuple: (key, name, threshold, emoji, color).
TIERS: list[tuple[str, str, int, str, str]] = [
    # Classic — first hour of play
    ("bronze",         "Bronze",         0,                "🥉", "#cd7f32"),
    ("silver",         "Silver",         100_000,          "⚪", "#c0c0c0"),
    ("gold",           "Gold",           500_000,          "🟡", "#f5b042"),
    ("platinum",       "Platinum",       1_500_000,        "💎", "#e5e4e2"),
    ("diamond",        "Diamond",        5_000_000,        "💠", "#5aa9ff"),
    # High-tier — committed grinders
    ("black_diamond",  "Black Diamond",  15_000_000,       "⬛", "#3d3d4d"),
    ("obsidian",       "Obsidian",       50_000_000,       "🌑", "#2a2438"),
    ("titanium",       "Titanium",       150_000_000,      "⚙",  "#aab2bd"),
    ("iridium",        "Iridium",        400_000_000,      "🔩", "#7d8a99"),
    ("palladium",      "Palladium",      1_000_000_000,    "🩶", "#b6bac2"),
    # Rare / Exotic
    ("mythril",        "Mythril",        2_500_000_000,    "✨", "#a3d8ff"),
    ("adamantium",     "Adamantium",     6_000_000_000,    "🛡",  "#5b6a8a"),
    ("vibranium",      "Vibranium",      15_000_000_000,   "🟣", "#8847ff"),
    ("aetherium",      "Aetherium",      40_000_000_000,   "🌫", "#a988ff"),
    ("celestium",      "Celestium",      100_000_000_000,  "☀",  "#ffd66e"),
    # Myth / Fantasy
    ("dragonsteel",    "Dragonsteel",    250_000_000_000,  "🐉", "#5b8a3a"),
    ("phoenix_core",   "Phoenix Core",   600_000_000_000,  "🔥", "#ff6b35"),
    ("leviathan",      "Leviathan",      1_500_000_000_000, "🐋", "#1d6b9c"),
    ("arcane",         "Arcane",         4_000_000_000_000, "🔮", "#b048ff"),
    ("eternal",        "Eternal",        10_000_000_000_000, "♾", "#d6c64a"),
    # Cosmic
    ("void",           "Void",           20_000_000_000_000,  "🌀", "#1a1024"),
    ("nebula",         "Nebula",         35_000_000_000_000,  "🌌", "#7340c4"),
    ("stellar",        "Stellar",        55_000_000_000_000,  "🌟", "#ffe85c"),
    ("galactic",       "Galactic",       75_000_000_000_000,  "🪐", "#d8a04a"),
    ("supernova",      "Supernova",      100_000_000_000_000, "💥", "#ff4757"),
    # Endgame — slowing ratios so 1 Q (10^15) is reachable but mythic
    ("quantum",        "Quantum",        200_000_000_000_000, "⚛",  "#00d4ff"),
    ("singularity",    "Singularity",    350_000_000_000_000, "🕳", "#0a0a14"),
    ("infinity",       "Infinity",       550_000_000_000_000, "♾",  "#a0e8ff"),
    ("transcendent",   "Transcendent",   800_000_000_000_000, "👁",  "#ffeacc"),
    ("godlike",        "Godlike",        1_000_000_000_000_000, "👑", "#ffd700"),  # 1 quadrillion
]


# ============================================================
# PER-TIER COINFLIP BET CAPS
# ============================================================
#
# Coinflip with no cap = guaranteed money printer via martingale
# (proven in production by an Igor session that pulled +4.4M in 5 minutes
# at 21:00 on 2026-04-27). Cap by tier kills the doubling spiral while
# rewarding genuine grinders with bigger limits.
#
# None = unlimited (Cosmic+ tiers earned the right to whale freely).

COINFLIP_MAX_BET_BY_TIER: dict[str, int | None] = {
    "bronze":         1_000,
    "silver":         2_500,
    "gold":           5_000,
    "platinum":       10_000,
    "diamond":        25_000,
    "black_diamond":  50_000,
    "obsidian":       100_000,
    "titanium":       200_000,
    "iridium":        500_000,
    "palladium":      1_000_000,
    "mythril":        2_000_000,
    "adamantium":     5_000_000,
    "vibranium":      10_000_000,
    "aetherium":      25_000_000,
    "celestium":      50_000_000,
    "dragonsteel":    100_000_000,
    "phoenix_core":   250_000_000,
    "leviathan":      500_000_000,
    "arcane":         1_000_000_000,
    "eternal":        2_500_000_000,
    # Cosmic + Endgame — no cap
    "void":           None,
    "nebula":         None,
    "stellar":        None,
    "galactic":       None,
    "supernova":      None,
    "quantum":        None,
    "singularity":    None,
    "infinity":       None,
    "transcendent":   None,
    "godlike":        None,
}


def coinflip_max_bet(wager: int) -> int | None:
    """Return MAX_BET (in coins) for a given lifetime_wager, or None for unlimited."""
    cur = get_tier(int(wager))
    return COINFLIP_MAX_BET_BY_TIER.get(cur["key"], 1_000)


def get_tier(wager: int) -> dict:
    """Return current tier info for a given lifetime wager."""
    wager = max(0, int(wager))
    cur = TIERS[0]
    for tier in TIERS:
        if wager >= tier[2]:
            cur = tier
        else:
            break
    key, name, threshold, emoji, color = cur
    return {
        "key": key,
        "name": name,
        "threshold": int(threshold),
        "emoji": emoji,
        "color": color,
    }


def get_progress(wager: int) -> dict:
    """Tier + next-tier progress info for the profile UI.

    Returns:
        current: tier dict for the achieved level
        next: tier dict for the next milestone (or None if at top)
        wager: clamped lifetime wager
        progress_pct: 0..100 percent toward `next`
        wager_in_tier: amount accumulated since `current` threshold
        span: amount of wager between current and next thresholds
    """
    wager = max(0, int(wager))
    cur_idx = 0
    for i, tier in enumerate(TIERS):
        if wager >= tier[2]:
            cur_idx = i
        else:
            break

    cur = TIERS[cur_idx]
    nxt = TIERS[cur_idx + 1] if cur_idx + 1 < len(TIERS) else None

    if nxt is None:
        return {
            "current": _to_dict(cur),
            "next": None,
            "wager": wager,
            "progress_pct": 100.0,
            "wager_in_tier": wager - int(cur[2]),
            "span": 0,
        }

    span = int(nxt[2]) - int(cur[2])
    in_tier = wager - int(cur[2])
    pct = (in_tier / span * 100.0) if span > 0 else 100.0
    return {
        "current": _to_dict(cur),
        "next": _to_dict(nxt),
        "wager": wager,
        "progress_pct": round(pct, 2),
        "wager_in_tier": in_tier,
        "span": span,
    }


def _to_dict(tier: tuple) -> dict:
    key, name, threshold, emoji, color = tier
    return {
        "key": key,
        "name": name,
        "threshold": int(threshold),
        "emoji": emoji,
        "color": color,
    }


# ============================================================
# SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_tiers.sql"
    if not sql_path.exists():
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)


# ============================================================
# Bulk helpers (used by leaderboards)
# ============================================================

async def get_wager(tg_id: int) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select lifetime_wager from economy_users where tg_id = $1", int(tg_id),
        )
    return int(row["lifetime_wager"]) if row else 0


async def bump_wager(conn, tg_id: int, bet: int) -> None:
    """Best-effort: increment lifetime_wager. Caller passes its own conn so the
    bump can ride on the same transaction as the bet itself if needed."""
    if bet <= 0:
        return
    await conn.execute(
        "update economy_users set lifetime_wager = lifetime_wager + $2 "
        "where tg_id = $1",
        int(tg_id), int(bet),
    )
