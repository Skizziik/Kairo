from __future__ import annotations

import json
import logging
from datetime import datetime

from app.ai import cerebras
from app.ai.prompts import EXTRACT_SYSTEM, build_system_prompt
from app.config import get_settings
from app.db import repos

log = logging.getLogger(__name__)


def _format_window(msgs: list[repos.MessageRow]) -> str:
    lines = []
    for m in msgs:
        who = m.username or m.first_name or f"user{m.tg_user_id}"
        tag = "BOT" if m.is_bot else f"@{who}"
        lines.append(f"{tag}: {m.text}")
    return "\n".join(lines)


async def answer_as_rip(
    chat_id: int,
    asker_id: int,
    asker_display: str,
    question: str,
) -> str:
    s = get_settings()
    summary, traits = await repos.get_profile(asker_id)
    window_msgs = await repos.recent_messages(chat_id, s.chat_history_limit)
    window = _format_window(window_msgs)

    system = build_system_prompt(
        asker_display=asker_display,
        asker_profile=summary,
        asker_traits=traits,
        memories=[],  # semantic memories disabled on free tier
        chat_window=window,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    return await cerebras.chat(messages, temperature=0.8, max_tokens=500)


async def summarize_recent(chat_id: int, limit: int = 80) -> str:
    window_msgs = await repos.recent_messages(chat_id, limit)
    if not window_msgs:
        return "Нет сообщений для пересказа."
    window = _format_window(window_msgs)
    messages = [
        {
            "role": "system",
            "content": (
                "Ты делаешь краткий пересказ последних сообщений чата RIP CS2. "
                "Пиши буллетами, 5–10 пунктов, на русском, с сохранением ников через @. "
                "Без воды, без оценок, факты и договорённости."
            ),
        },
        {"role": "user", "content": f"Пересчитай о чём был разговор:\n\n{window}"},
    ]
    return await cerebras.chat(messages, temperature=0.3, max_tokens=600)


async def extract_and_store(chat_id: int, window_size: int = 60) -> int:
    """Periodic job — pull recent messages, ask model to extract per-user facts,
    upsert profiles. Returns number of profiles updated.

    Semantic memories table is left empty on the free tier (no embeddings). The
    per-user `summary` and `traits` on user_profiles carry all the personality
    state the bot actually uses when answering."""
    window_msgs = await repos.recent_messages(chat_id, window_size)
    if len(window_msgs) < 5:
        return 0
    window = _format_window(window_msgs)
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": window},
    ]
    raw = await cerebras.chat(messages, temperature=0.2, max_tokens=1200)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("extractor returned non-JSON: %s", raw[:200])
        return 0

    profiles = data.get("profiles") or []
    updated = 0
    for p in profiles:
        try:
            uid = int(p["tg_user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        summary = (p.get("summary") or "").strip()
        traits = p.get("traits") or {}

        if summary or traits:
            await repos.upsert_profile(uid, summary, traits if isinstance(traits, dict) else {})
            updated += 1
    log.info("memory extraction: %d profiles updated at %s", updated, datetime.utcnow().isoformat())
    return updated
