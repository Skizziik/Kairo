"""Всё или Ничего — coin flip с банком.

Ставка = ВЕСЬ баланс игрока (нельзя выбрать сумму). 50/50 шанс.
- Выпала «решка» (win) → balance × 2
- Выпал «орёл» (lose) → balance = 0

Поддерживает любые размеры балансов (numeric column без precision limit).
Транзакция атомарная — никаких race conditions.
"""
from __future__ import annotations

import logging
import random

from app.db.client import pool

log = logging.getLogger(__name__)


async def play(tg_id: int) -> dict:
    """Выполняет один coin flip. Атомарно.

    Returns:
        ok: bool
        won: bool   — выиграл ли игрок
        prev_balance: int — что было до игры
        new_balance: int  — что стало после
        delta: int — изменение (+balance или -balance)
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния — сначала зайди в кейс или другую игру"}

            prev = int(row["balance"])
            if prev <= 0:
                return {"ok": False, "error": "Нечего ставить — у тебя 0 на балансе"}

            won = random.random() < 0.5

            if won:
                # balance *= 2 — делаем на стороне SQL чтобы не ловить precision
                await conn.execute(
                    "update economy_users set balance = balance * 2, "
                    "total_earned = total_earned + $2 "
                    "where tg_id = $1",
                    tg_id, prev,
                )
                new_bal = prev * 2
                delta = prev
            else:
                await conn.execute(
                    "update economy_users set balance = 0, "
                    "total_spent = total_spent + $2 "
                    "where tg_id = $1",
                    tg_id, prev,
                )
                new_bal = 0
                delta = -prev

            # Audit log (best-effort)
            try:
                await conn.execute(
                    "insert into economy_transactions "
                    "(user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'all_or_nothing', $3, $4)",
                    tg_id, delta,
                    "win" if won else "lose",
                    new_bal,
                )
            except Exception:
                log.exception("aon: transaction log failed")

    log.info(
        "aon: tg_id=%s %s prev=%s new=%s",
        tg_id, "WIN" if won else "LOSE", prev, new_bal,
    )

    return {
        "ok": True,
        "won": won,
        "prev_balance": prev,
        "new_balance": new_bal,
        "delta": delta,
    }
