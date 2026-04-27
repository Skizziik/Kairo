"""CS Gates — 6×5 pay-anywhere tumble slot inspired by Gates of Olympus.

Mechanics:
- 6 reels × 5 rows grid, 30 positions
- Pay-anywhere: 8+ same symbols ANYWHERE on the grid wins (count-based, not line-based)
- Tumble cascade: winning symbols explode, new ones drop from top, chain continues
- Multiplier Orbs: random coins with 2x-500x land on grid; all orbs in a chain are SUMMED then applied to total win of that whole spin
- Scatter (💣 bomb): 4+ anywhere triggers 15 Free Spins, also pays 3x/10x/100x
- Free Spins: 15 spins with PERSISTENT multiplier accumulator — orbs keep adding across all 15 spins
- Bonus Buy: pay 100× bet → instant 15 FS
- Max win cap: 5000× bet
"""
from __future__ import annotations

import logging
import random
from typing import Literal

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

GRID_COLS = 6
GRID_ROWS = 5
GRID_SIZE = GRID_COLS * GRID_ROWS  # 30

# Symbols: (key, icon, display_name)
SYMBOLS: list[tuple[str, str, str]] = [
    ("scatter",     "💣", "C4 Бомба"),
    ("milspec",     "🟦", "Mil-spec"),
    ("classified",  "🟪", "Classified"),
    ("covert",      "🟥", "Covert-gem"),
    ("m4",          "🔫", "M4A4"),
    ("gloves",      "🧤", "Gloves"),
    ("ak",          "🎯", "AK-47"),
    ("awp",         "🏆", "AWP"),
    ("knife",       "🔪", "Knife"),
]
SYMBOL_KEYS = [s[0] for s in SYMBOLS]
SYMBOL_ICON = {k: ic for k, ic, _ in SYMBOLS}

# Spawn weights (sum = 100). Tuned for RTP ~96%.
WEIGHTS: dict[str, int] = {
    "scatter":     2,    # FS trigger ~1 in 300 spins base, more often with bonus buy
    "milspec":     22,
    "classified":  18,
    "covert":      15,
    "m4":          13,
    "gloves":      10,
    "ak":          9,
    "awp":         7,
    "knife":       4,
}
assert sum(WEIGHTS.values()) == 100, f"weights must sum to 100, got {sum(WEIGHTS.values())}"

# Pay table — strict (~55-60% RTP target). Knife jackpots still chunky but base wins minimal.
PAYOUTS: dict[str, list[tuple[int, float]]] = {
    "milspec":    [(8, 0.03), (10, 0.07), (12, 0.25)],
    "classified": [(8, 0.05), (10, 0.12), (12, 0.5)],
    "covert":     [(8, 0.08), (10, 0.20), (12, 1)],
    "m4":         [(8, 0.13), (10, 0.25), (12, 1.3)],
    "gloves":     [(8, 0.18), (10, 0.40), (12, 1.8)],
    "ak":         [(8, 0.25), (10, 0.75), (12, 2.5)],
    "awp":        [(8, 0.5),  (10, 1.5),  (12, 4)],
    "knife":      [(8, 1.5),  (10, 3),    (12, 7)],
}

# Scatter payouts (also triggers FS at 4+)
SCATTER_PAYOUT = {4: 3, 5: 10, 6: 100}  # bet multiplier
SCATTER_FS_TRIGGER = 4
FS_SPINS_AWARDED = 15
FS_RETRIGGER_SCATTERS = 3  # 3+ scatters during FS adds +5 spins
FS_RETRIGGER_ADD = 5

# Multiplier Orbs — (value, weight). Small orbs dominate, 500x is lottery-tier.
ORBS: list[tuple[int, float]] = [
    (2, 150), (3, 80), (4, 40), (5, 20), (6, 12), (8, 6), (10, 3),
    (12, 2), (15, 1), (20, 0.5), (25, 0.3), (50, 0.1), (100, 0.03), (250, 0.01), (500, 0.005),
]
ORB_CHANCE_BASE = 0.06  # 6% per tumble in base
ORB_CHANCE_FS = 0.22    # 22% per tumble in FS — this is where multiplier builds up

MAX_WIN_CAP = 5000

# Bonus buy variants
BONUS_BUY_REGULAR = {
    "cost_mult": 120,    # 120× bet (operator-tuned 2026-04-27 — was 70×, exploited)
    "spins": 15,         # classic Gates of Olympus FS count
    "start_mult": 0,     # multiplier starts at 0, accumulates as orbs land
}
BONUS_BUY_PREMIUM = {
    "cost_mult": 360,    # 360× bet (operator-tuned 2026-04-27 — was 220×)
    "spins": 25,         # more spins
    "start_mult": 10,    # starts at x10, accumulates on top
}


# ============================================================
# ENGINE
# ============================================================

def _roll_symbol(rng: random.Random) -> str:
    r = rng.uniform(0, 100)
    cum = 0
    for key, weight in WEIGHTS.items():
        cum += weight
        if r <= cum:
            return key
    return "milspec"  # fallback (shouldn't happen)


def _new_grid(rng: random.Random) -> list[list[str]]:
    """Return 2D grid [col][row]. Column-major so we can drop symbols."""
    return [[_roll_symbol(rng) for _ in range(GRID_ROWS)] for _ in range(GRID_COLS)]


def _count_symbols(grid: list[list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for col in grid:
        for sym in col:
            if sym is not None:
                counts[sym] = counts.get(sym, 0) + 1
    return counts


def _find_wins(grid: list[list[str]]) -> list[dict]:
    """Return list of {symbol, count, multiplier} for paying symbols (not scatter)."""
    counts = _count_symbols(grid)
    wins = []
    for sym, count in counts.items():
        if sym == "scatter":
            continue  # scatter handled separately
        tiers = PAYOUTS.get(sym)
        if not tiers:
            continue
        best_mult = 0.0
        for threshold, mult in tiers:
            if count >= threshold:
                best_mult = mult
        if best_mult > 0:
            wins.append({"symbol": sym, "count": count, "multiplier": best_mult})
    return wins


def _explode_and_tumble(
    grid: list[list[str]],
    win_symbols: set[str],
    rng: random.Random,
) -> tuple[list[list[str]], list[list[int]]]:
    """Remove winning symbols, drop remainder down, refill from top.
    Returns (new_grid, exploded_positions_per_col) — positions list for UI animation."""
    exploded: list[list[int]] = [[] for _ in range(GRID_COLS)]
    for c in range(GRID_COLS):
        # Collect survivors bottom-up
        survivors = []
        for r in range(GRID_ROWS):
            if grid[c][r] in win_symbols:
                exploded[c].append(r)
            else:
                survivors.append(grid[c][r])
        # Fill top with new symbols
        new_count = GRID_ROWS - len(survivors)
        new_symbols = [_roll_symbol(rng) for _ in range(new_count)]
        grid[c] = new_symbols + survivors  # new on top, survivors fall to bottom
    return grid, exploded


def _roll_orb(rng: random.Random) -> int:
    """Pick an orb value by weight."""
    total = sum(w for _, w in ORBS)
    r = rng.uniform(0, total)
    cum = 0
    for value, weight in ORBS:
        cum += weight
        if r <= cum:
            return value
    return ORBS[0][0]


def _maybe_drop_orb(in_fs: bool, rng: random.Random) -> tuple[int, tuple[int, int]] | None:
    """With probability, drop a multiplier orb at random grid position.
    Returns (value, (col, row)) or None."""
    chance = ORB_CHANCE_FS if in_fs else ORB_CHANCE_BASE
    if rng.random() > chance:
        return None
    val = _roll_orb(rng)
    pos = (rng.randrange(GRID_COLS), rng.randrange(GRID_ROWS))
    return val, pos


# ============================================================
# SPIN — full resolve of a single base spin OR a single FS spin
# ============================================================

def _resolve_spin(
    bet: int,
    in_fs: bool,
    persistent_mult: int,
    rng: random.Random,
) -> dict:
    """Play ONE spin (with tumbles). Returns full sequence for UI replay.

    In base: `persistent_mult` is ignored at end; sum of orbs this spin is applied to this spin's win.
    In FS: `persistent_mult` accumulates — all orbs across all FS spins sum up and apply to every win.
    """
    grid = _new_grid(rng)
    tumbles: list[dict] = []  # each: {grid, wins, win_amount, orbs, exploded}
    total_win = 0
    spin_orbs_sum = 0  # only used in base — orb sum applied to THIS spin's total

    while True:
        # Find wins
        wins = _find_wins(grid)
        # Roll orb (can drop even on non-winning tumble — visual candy)
        orb = _maybe_drop_orb(in_fs, rng)

        if not wins:
            # Last snapshot with orb (if any) for visualization
            if orb is not None:
                val, pos = orb
                spin_orbs_sum += val
                if in_fs:
                    persistent_mult += val
                tumbles.append({
                    "grid": [col[:] for col in grid],
                    "wins": [],
                    "win_amount": 0,
                    "orbs": [{"value": val, "col": pos[0], "row": pos[1]}],
                    "exploded": [[] for _ in range(GRID_COLS)],
                })
            break

        # Collect win amount at this tumble
        win_base = sum(w["multiplier"] for w in wins) * bet
        orbs_this = []
        if orb is not None:
            val, pos = orb
            orbs_this.append({"value": val, "col": pos[0], "row": pos[1]})
            spin_orbs_sum += val
            if in_fs:
                persistent_mult += val

        total_win += int(win_base)

        # Explode + tumble
        win_syms = {w["symbol"] for w in wins}
        new_grid_snapshot = [col[:] for col in grid]  # pre-explode snapshot
        grid, exploded = _explode_and_tumble(grid, win_syms, rng)

        tumbles.append({
            "grid": new_grid_snapshot,
            "wins": wins,
            "win_amount": int(win_base),
            "orbs": orbs_this,
            "exploded": exploded,
            "post_grid": [col[:] for col in grid],
        })

    # Apply orb multiplier to total
    if in_fs:
        # In FS: final multiplier applied at END of ALL 15 spins, not per spin.
        # So here we just report this spin's raw base win; multiplier applied outside.
        final_win = total_win
    else:
        # Base: apply spin's orb sum as (1 + orbs_sum) multiplier to total_win
        # Wait — Pragmatic behavior: orbs are summed, then applied directly.
        # E.g., total_win=5 bet, orbs_sum=10 → 5*10 = 50. Orbs REPLACE implicit 1x.
        # Actually GoO: "multipliers applied to total spin win". If 0 orbs → no mult (×1).
        if spin_orbs_sum > 0:
            final_win = total_win * spin_orbs_sum
        else:
            final_win = total_win

    # Scatter count for this spin
    final_grid = [col[:] for col in grid]  # grid state after all tumbles
    scatter_count = sum(1 for col in final_grid for s in col if s == "scatter")

    return {
        "tumbles": tumbles,
        "total_win_before_mult": total_win,
        "orbs_sum": spin_orbs_sum,
        "final_win": final_win,
        "scatter_count": scatter_count,
        "persistent_mult": persistent_mult,
        "final_grid": final_grid,
    }


# ============================================================
# MAIN ENTRY: play a full session (base spin + optional FS)
# ============================================================

MAX_BET = 10_000  # hard cap on per-spin bet


async def spin(user_id: int, bet: int, bonus_buy: bool = False, bonus_type: str = "regular") -> dict:
    """Full play: deduct cost, run base spin (unless bonus_buy), trigger FS if 4+ scatters,
    accumulate multipliers during FS, credit winnings, return full visualization data."""
    if bet <= 0:
        return {"ok": False, "error": "Bet must be positive"}
    if bet > MAX_BET:
        return {"ok": False, "error": f"Max bet {MAX_BET:,}"}

    # Resolve bonus buy parameters
    bb = BONUS_BUY_PREMIUM if bonus_type == "premium" else BONUS_BUY_REGULAR
    if bonus_buy:
        cost = bet * bb["cost_mult"]
        fs_spins_count = bb["spins"]
        fs_start_mult = bb["start_mult"]
    else:
        cost = bet
        fs_spins_count = FS_SPINS_AWARDED  # scatter-triggered FS use default 15
        fs_start_mult = 0

    async with pool().acquire() as conn:
        bal_row = await conn.fetchrow(
            "select balance from economy_users where tg_id = $1", user_id,
        )
        if bal_row is None or int(bal_row["balance"]) < cost:
            return {"ok": False, "error": "Not enough coins", "cost": cost}

    rng = random.Random()
    base_spin = None
    scatter_payout = 0
    fs_triggered = bonus_buy
    fs_trigger_reason = ("bonus_buy_" + bonus_type) if bonus_buy else None

    total_win = 0

    # ========== BASE SPIN (skipped on bonus buy) ==========
    if not bonus_buy:
        base_spin = _resolve_spin(bet, in_fs=False, persistent_mult=0, rng=rng)
        total_win += base_spin["final_win"]

        # Scatter pay
        scat = base_spin["scatter_count"]
        if scat >= 4:
            mult = SCATTER_PAYOUT.get(min(scat, 6), 100)
            scatter_payout = mult * bet
            total_win += scatter_payout
            fs_triggered = True
            fs_trigger_reason = "scatters"

    # ========== FREE SPINS ==========
    fs_data = None
    if fs_triggered:
        fs_spins_left = fs_spins_count
        persistent_mult = fs_start_mult
        fs_spins: list[dict] = []
        fs_total_base = 0  # raw win amount (before applying accumulated mult)

        while fs_spins_left > 0:
            fs_spins_left -= 1
            s = _resolve_spin(bet, in_fs=True, persistent_mult=persistent_mult, rng=rng)
            persistent_mult = s["persistent_mult"]
            fs_total_base += s["final_win"]
            # Retriggers disabled — always exactly the promised number of spins
            fs_spins.append(s)

        # Final FS win = raw total × (1 + accumulated mult) OR × mult only?
        # GoO behavior: final payout = raw × accumulated multiplier. If no orbs (mult=0), win counts as 0?
        # Actually mult 0 means no orb dropped; we still pay raw base. Fix: if mult=0, use ×1.
        fs_final_mult = persistent_mult if persistent_mult > 0 else 1
        fs_final_win = fs_total_base * fs_final_mult
        total_win += fs_final_win

        fs_data = {
            "spins": fs_spins,
            "total_base": fs_total_base,
            "accumulated_mult": persistent_mult,
            "applied_mult": fs_final_mult,
            "final_win": fs_final_win,
            "trigger_reason": fs_trigger_reason,
            "start_mult": fs_start_mult,
            "spins_count": fs_spins_count,
            "variant": bonus_type if bonus_buy else "scatter",
        }

    # ========== APPLY MAX WIN CAP ==========
    max_win = bet * MAX_WIN_CAP
    capped = False
    if total_win > max_win:
        total_win = max_win
        capped = True

    net_delta = total_win - cost

    # ========== COMMIT TO DB ==========
    async with pool().acquire() as conn:
        async with conn.transaction():
            # Re-check balance to avoid race with other txns
            cur = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                user_id,
            )
            if cur is None or int(cur["balance"]) < cost:
                return {"ok": False, "error": "Not enough coins (race)"}
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + greatest($2, 0), "
                "total_spent = total_spent + greatest(-$2, 0) "
                "where tg_id = $1 returning balance",
                user_id, net_delta,
            )
            new_bal = int(new_bal_row["balance"])
            reason = f"megaslot_{'buy' if bonus_buy else 'spin'}_{'fs' if fs_triggered else 'base'}"
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, net_delta, reason, new_bal,
            )

    try:
        from app.economy import audit as _audit
        await _audit.log_bet(
            user_id, "megaslot", bet=cost, win=total_win, net=net_delta,
            details={
                "bonus_buy": bonus_buy,
                "bonus_type": bonus_type if bonus_buy else None,
                "fs_triggered": fs_triggered,
                "fs_total_base": (fs_data or {}).get("total_base") if fs_data else None,
                "fs_mult": (fs_data or {}).get("applied_mult") if fs_data else None,
                "scatter_payout": scatter_payout,
                "capped": capped,
                "mult": (total_win // bet) if bet > 0 else 0,
            },
            balance_after=new_bal,
        )
    except Exception:
        pass
    return {
        "ok": True,
        "bet": bet,
        "cost": cost,
        "bonus_buy": bonus_buy,
        "base_spin": base_spin,
        "scatter_payout": scatter_payout,
        "fs_triggered": fs_triggered,
        "fs_trigger_reason": fs_trigger_reason,
        "fs": fs_data,
        "total_win": total_win,
        "delta": net_delta,
        "new_balance": new_bal,
        "capped": capped,
        "max_win": max_win,
    }


# ============================================================
# STATIC CONFIG — surfaced to client for UI
# ============================================================

# Map slot weapon symbol → weapon name in catalog. Server picks the highest-priced
# skin for that weapon as the visual representative.
SYMBOL_TO_WEAPON = {
    "knife":  "★ Karambit",   # prefix '★' = knife category in CSGO-API naming
    "awp":    "AWP",
    "ak":     "AK-47",
    "gloves": "★ Sport Gloves",
    "m4":     "M4A4",
}


async def _get_symbol_image_map() -> dict[str, str]:
    """Return {symbol_key: image_url} for weapon symbols. Gems/scatter get empty strings."""
    result: dict[str, str] = {}
    async with pool().acquire() as conn:
        for sym_key, weapon_name in SYMBOL_TO_WEAPON.items():
            # Knives/gloves use category filter; regular weapons use weapon-name match
            if sym_key in ("knife", "gloves"):
                row = await conn.fetchrow(
                    "select image_url from economy_skins_catalog "
                    "where active and category = $1 and image_url is not null "
                    "order by base_price desc limit 1",
                    "knife" if sym_key == "knife" else "gloves",
                )
            else:
                row = await conn.fetchrow(
                    "select image_url from economy_skins_catalog "
                    "where active and weapon = $1 and image_url is not null "
                    "order by base_price desc limit 1",
                    weapon_name,
                )
            if row and row["image_url"]:
                result[sym_key] = row["image_url"]
            else:
                result[sym_key] = ""
    return result


async def get_config() -> dict:
    images = await _get_symbol_image_map()
    return {
        "grid_cols": GRID_COLS,
        "grid_rows": GRID_ROWS,
        "symbols": [
            {
                "key": k,
                "icon": ic,
                "name": n,
                "image_url": images.get(k, ""),
            }
            for k, ic, n in SYMBOLS
        ],
        "payouts": PAYOUTS,
        "scatter_payout": SCATTER_PAYOUT,
        "scatter_fs_trigger": SCATTER_FS_TRIGGER,
        "fs_spins_awarded": FS_SPINS_AWARDED,
        "bonus_buy_regular": BONUS_BUY_REGULAR,
        "bonus_buy_premium": BONUS_BUY_PREMIUM,
        "max_win_mult": MAX_WIN_CAP,
        "orb_values": [v for v, _ in ORBS],
    }
