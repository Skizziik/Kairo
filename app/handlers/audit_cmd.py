"""/audit — admin-only bet activity report.

Usage:
    /audit @username           → last 1h
    /audit @username 30m       → last 30 minutes
    /audit @username 7d        → last 7 days
    /audit @username 15        → last 15 minutes (bare number = minutes)
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from html import escape

from app.config import get_settings
from app.economy import audit as _audit

router = Router(name="audit_cmd")
log = logging.getLogger(__name__)


@router.message(Command("audit"))
async def cmd_audit(msg: Message) -> None:
    if msg.from_user is None:
        return
    s = get_settings()
    if msg.from_user.id not in s.admin_id_set:
        await msg.reply("Только для админов.")
        return

    text = (msg.text or "").strip()
    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        await msg.reply(
            "Использование: <code>/audit @username [период]</code>\n"
            "Период: <code>30m</code>, <code>1h</code>, <code>7d</code>, <code>15</code> (мин). По умолчанию — 1h."
        )
        return

    name_query = parts[1]
    period_arg = parts[2] if len(parts) >= 3 else None
    period_seconds = _audit.parse_period(period_arg)

    user_id, display_name = await _audit.resolve_user_by_name(name_query)
    if user_id is None:
        await msg.reply(f"Не нашёл игрока по запросу <code>{escape(name_query)}</code>")
        return

    try:
        report = await _audit.build_report(
            user_id, period_seconds=period_seconds, display_name=display_name,
        )
    except Exception as e:
        log.exception("audit report failed")
        await msg.reply(f"Ошибка отчёта: <code>{escape(str(e))}</code>")
        return

    # Telegram message limit ~4096 chars — truncate if needed
    if len(report) > 3900:
        report = report[:3900] + "\n…(обрезано)"
    await msg.reply(report)
