"""Bet audit log — per-bet detail for casino games (7-day retention).

Each casino-game endpoint calls `log_bet(...)` after committing the
transaction. Failures are swallowed (audit is best-effort, must never
break gameplay). Background `cleanup_loop` deletes rows >7 days old.

The /audit Telegram command queries this table via `build_report(...)`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.client import pool

log = logging.getLogger(__name__)


RETENTION_DAYS = 7


async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_audit.sql"
    if not sql_path.exists():
        log.warning("audit migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("audit schema ensured")


# ============================================================
# WRITE — fire-and-forget logging
# ============================================================

async def log_bet(
    user_id: int,
    game: str,
    bet: int,
    win: int,
    net: int,
    details: dict | None = None,
    balance_after: int | None = None,
) -> None:
    """Write one audit row + bump lifetime_wager. Best-effort — never raises.

    The wager bump and the audit insert share a single connection but are
    independently try-wrapped so a missing audit table won't block the wager
    counter (and vice versa).
    """
    try:
        async with pool().acquire() as conn:
            try:
                await conn.execute(
                    "insert into bet_audit "
                    "(user_id, game, bet, win, net, details, balance_after) "
                    "values ($1, $2, $3, $4, $5, $6::jsonb, $7)",
                    int(user_id), str(game),
                    int(bet), int(win), int(net),
                    json.dumps(details or {}, default=_json_default),
                    int(balance_after) if balance_after is not None else None,
                )
            except Exception as e:
                log.debug("audit insert skipped: %s", e)
            try:
                if int(bet) > 0:
                    await conn.execute(
                        "update economy_users set lifetime_wager = lifetime_wager + $2 "
                        "where tg_id = $1",
                        int(user_id), int(bet),
                    )
            except Exception as e:
                log.debug("lifetime_wager bump skipped: %s", e)
    except Exception as e:
        log.debug("audit log_bet skipped: %s", e)


def _json_default(o):
    if isinstance(o, (datetime,)):
        return o.isoformat()
    return str(o)


# ============================================================
# CLEANUP — runs hourly in background
# ============================================================

async def cleanup_old() -> int:
    """Delete bet_audit rows older than RETENTION_DAYS."""
    async with pool().acquire() as conn:
        n = await conn.fetchval(
            "with d as ("
            "  delete from bet_audit "
            f"  where created_at < now() - interval '{RETENTION_DAYS} days' "
            "  returning 1"
            ") select count(*) from d"
        )
    return int(n or 0)


async def cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(3600)  # hourly
            killed = await cleanup_old()
            if killed:
                log.info("audit: cleaned %d rows older than %dd", killed, RETENTION_DAYS)
        except Exception:
            log.exception("audit cleanup tick failed")


# ============================================================
# REPORT — used by the /audit Telegram command
# ============================================================

GAME_NAMES = {
    "coinflip": "🪙 Coinflip",
    "slots":    "🎰 Слоты",
    "crash":    "💥 Crash",
    "megaslot": "⚡ CS Gates",
    "mines":    "💣 Mines",
    "plinko":   "🟡 Plinko",
    "cf_pvp":   "⚔️ Coinflip 1v1",
    "wheel":    "🎡 Wheel",
}


def _fmt_int(n: int) -> str:
    s = f"{int(n):,}".replace(",", " ")
    return s


def _details_blurb(game: str, det: dict) -> str:
    """One-line human summary of game-specific detail."""
    if not det:
        return ""
    if game == "coinflip":
        return f"{det.get('side','?')} → {det.get('result','?')}"
    if game == "slots":
        reels = det.get("reels") or []
        return " ".join(reels)
    if game == "crash":
        t = det.get("target", "?")
        c = det.get("crash_point", "?")
        return f"target {t}× crash {c}×"
    if game == "megaslot":
        if det.get("bonus_buy"):
            return f"BUY {det.get('bonus_type','reg')} ×{det.get('mult','?')}"
        return f"spin ×{det.get('mult','?')}"
    if game == "mines":
        return f"bombs={det.get('bombs','?')} reveals={det.get('revealed_count','?')} mult={det.get('multiplier','?')}×"
    if game == "plinko":
        return f"{det.get('mode','?')} bucket {det.get('bucket','?')} ×{det.get('multiplier','?')}"
    if game == "cf_pvp":
        return f"vs {det.get('opponent_name','?')} pot {_fmt_int(det.get('pot_value',0))}"
    return ""


async def build_report(
    user_id: int,
    period_seconds: int = 3600,
    display_name: str | None = None,
) -> str:
    """Markdown summary of a player's recent betting activity."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=int(period_seconds))
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select game, bet, win, net, details, balance_after, created_at "
            "from bet_audit "
            "where user_id = $1 and created_at >= $2 "
            "order by created_at desc",
            int(user_id), cutoff,
        )
    if not rows:
        return f"<b>{display_name or user_id}</b> — за указанный период ставок не было."

    by_game_count   = defaultdict(int)
    by_game_wagered = defaultdict(int)
    by_game_won     = defaultdict(int)
    by_game_net     = defaultdict(int)
    total_bets      = len(rows)
    total_wagered   = 0
    total_won       = 0
    total_net       = 0
    biggest_win     = None
    biggest_loss    = None
    sample_rows     = []

    for r in rows:
        det = r["details"]
        if isinstance(det, str):
            try: det = json.loads(det)
            except Exception: det = {}
        bet, win, net = int(r["bet"]), int(r["win"]), int(r["net"])
        g = r["game"]
        by_game_count[g]   += 1
        by_game_wagered[g] += bet
        by_game_won[g]     += win
        by_game_net[g]     += net
        total_wagered += bet
        total_won     += win
        total_net     += net
        sample_rows.append({
            "ts": r["created_at"], "game": g, "bet": bet, "win": win, "net": net,
            "details": det, "balance_after": r["balance_after"],
        })
        if net > 0 and (biggest_win is None or net > biggest_win["net"]):
            biggest_win = sample_rows[-1]
        if net < 0 and (biggest_loss is None or net < biggest_loss["net"]):
            biggest_loss = sample_rows[-1]

    period_h = int(period_seconds // 3600)
    period_m = int((period_seconds % 3600) // 60)
    period_label = (
        f"{period_h}ч{(' '+str(period_m)+'м') if period_m else ''}"
        if period_h else f"{period_m}мин"
    )

    name = display_name or f"user{user_id}"
    sign = "+" if total_net >= 0 else ""
    rtp = (total_won / total_wagered * 100) if total_wagered > 0 else 0
    out = [
        f"📊 <b>{name}</b> — за {period_label}",
        f"",
        f"Ставок: <b>{total_bets}</b> · Поставлено: <b>{_fmt_int(total_wagered)}</b> 🪙",
        f"Выиграно: <b>{_fmt_int(total_won)}</b> · Net: <b>{sign}{_fmt_int(total_net)}</b> · RTP: <b>{rtp:.1f}%</b>",
        f"",
        f"<b>По играм:</b>",
    ]
    # Sort games by absolute net (most impactful first)
    for g in sorted(by_game_count.keys(), key=lambda k: abs(by_game_net[k]), reverse=True):
        nm = GAME_NAMES.get(g, g)
        n = by_game_count[g]
        w = by_game_wagered[g]
        net_g = by_game_net[g]
        sign_g = "+" if net_g >= 0 else ""
        out.append(f"  {nm}: {n} ставок, поставил {_fmt_int(w)}, net <b>{sign_g}{_fmt_int(net_g)}</b>")

    if biggest_win:
        out.append("")
        out.append(f"🏆 <b>Топ-винна:</b>")
        ts = biggest_win["ts"].astimezone(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
        det_str = _details_blurb(biggest_win["game"], biggest_win["details"])
        out.append(f"  {ts} {GAME_NAMES.get(biggest_win['game'], biggest_win['game'])} ставка {_fmt_int(biggest_win['bet'])} → +{_fmt_int(biggest_win['net'])} ({det_str})")
    if biggest_loss:
        out.append(f"💀 <b>Топ-лосс:</b>")
        ts = biggest_loss["ts"].astimezone(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
        det_str = _details_blurb(biggest_loss["game"], biggest_loss["details"])
        out.append(f"  {ts} {GAME_NAMES.get(biggest_loss['game'], biggest_loss['game'])} ставка {_fmt_int(biggest_loss['bet'])} → {_fmt_int(biggest_loss['net'])} ({det_str})")

    # Recent 10 bets (chronological tail)
    out.append("")
    out.append(f"<b>Последние 10 ставок:</b>")
    for s in sample_rows[:10]:
        ts = s["ts"].astimezone(timezone(timedelta(hours=3))).strftime("%H:%M:%S")
        sign_s = "+" if s["net"] >= 0 else ""
        det_str = _details_blurb(s["game"], s["details"])
        if det_str:
            det_str = " · " + det_str
        out.append(f"  {ts} {GAME_NAMES.get(s['game'], s['game'])}: ставка {_fmt_int(s['bet'])} → <b>{sign_s}{_fmt_int(s['net'])}</b>{det_str}")

    return "\n".join(out)


# ============================================================
# Helper to find a user by username/first_name (case-insensitive substring)
# ============================================================

async def resolve_user_by_name(query: str) -> tuple[int | None, str | None]:
    """Return (tg_id, display_name) for username/first_name match. Strips '@' prefix."""
    q = query.strip().lstrip("@").lower()
    if not q:
        return None, None
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select tg_id, username, first_name from users "
            "where lower(coalesce(username,'')) = $1 "
            "   or lower(coalesce(username,'')) like $2 "
            "   or lower(coalesce(first_name,'')) like $2 "
            "order by case when lower(coalesce(username,'')) = $1 then 0 else 1 end "
            "limit 1",
            q, f"%{q}%",
        )
    if row is None:
        return None, None
    name = row["first_name"] or (("@" + row["username"]) if row["username"] else f"user{row['tg_id']}")
    return int(row["tg_id"]), name


def parse_period(s: str | None) -> int:
    """'1h', '15m', '7d', '30s' → seconds. Default = 3600 (1h)."""
    if not s:
        return 3600
    s = s.strip().lower()
    try:
        if s.endswith("d"):
            return int(s[:-1]) * 86400
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("s"):
            return int(s[:-1])
        return int(s) * 60   # bare number = minutes
    except ValueError:
        return 3600
