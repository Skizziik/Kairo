from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

from app.ai import llm
from app.ai.embeddings import embed, embed_one
from app.ai.prompts import EXTRACT_SYSTEM, build_system_prompt
from app.config import get_settings
from app.db import repos
from app.db.client import pool

log = logging.getLogger(__name__)


# Openers are persisted per-chat in DB (bot_chat_state table) so they survive
# restarts. See repos.push_opener / repos.get_recent_openers.

# Openers that are banned in pole position — strip them if model ignores.
BANNED_OPENER_REGEX = re.compile(
    r"^(?:[Ээ]+\s*бо[йя][,.\-—!\s]+|"
    r"[Нн]у\s+ты\s+и?\s*рак[,.\-—!\s]+|"
    r"[Нн]у\s+ты\s+и?\s*кринж[,.\-—!\s]+)",
    re.IGNORECASE,
)

# Words/phrases the bot keeps over-using. If found, we rewrite.
BANNED_PHRASES = [
    re.compile(r"\bдесматч\w*", re.IGNORECASE),
    re.compile(r"\bфлэшк[аеуи]\s+в\s+мид", re.IGNORECASE),
    re.compile(r"\bтакси\s+за\s+200\s*к", re.IGNORECASE),
]


def _first_word(text: str) -> str:
    m = re.match(r"\s*(\S+)", text)
    return (m.group(1).lower().rstrip(",.!?;:—-") if m else "")


def _sanitize(text: str) -> str:
    """Strip accidental command-like prefixes AND banned opener phrases."""
    t = text.lstrip()
    # repeatedly drop leading command tokens like "/ai", "/ai@bot", "/cmd"
    while t.startswith("/"):
        space = t.find(" ")
        t = t[space + 1 :].lstrip() if space > 0 else ""
    # strip banned opener phrases ("Ээ бой, ...")
    t_new = BANNED_OPENER_REGEX.sub("", t).lstrip()
    if t_new and t_new != t:
        # capitalize first letter to preserve sentence shape
        t_new = t_new[0].upper() + t_new[1:]
        t = t_new
    return t or text.strip()


def _has_banned_phrase(text: str) -> bool:
    return any(p.search(text) for p in BANNED_PHRASES)


def _format_window(msgs: list[repos.MessageRow], show_ids: bool = False) -> str:
    lines = []
    for m in msgs:
        who = m.username or m.first_name or f"user{m.tg_user_id}"
        if m.is_bot:
            tag = "BOT"
        else:
            tag = f"[id={m.tg_user_id}] @{who}" if show_ids else f"@{who}"
        lines.append(f"{tag}: {m.text}")
    return "\n".join(lines)


async def answer_as_rip(
    chat_id: int,
    asker_id: int,
    asker_display: str,
    question: str,
) -> str:
    s = get_settings()

    async def _mem_count() -> int:
        async with pool().acquire() as conn:
            n = await conn.fetchval("select count(*) from memories where user_id = $1", asker_id)
        return int(n or 0)

    profile_task = asyncio.create_task(repos.get_profile(asker_id))
    history_task = asyncio.create_task(repos.recent_messages(chat_id, s.chat_history_limit))
    mem_count_task = asyncio.create_task(_mem_count())

    summary, traits = await profile_task
    window_msgs = await history_task
    mem_count = await mem_count_task
    window = _format_window(window_msgs)

    seen: set[str] = set()
    members: list[str] = []
    for m in window_msgs:
        if m.is_bot:
            continue
        nick = m.username or m.first_name
        if not nick:
            continue
        tag = f"@{nick}" if m.username else nick
        if tag not in seen:
            seen.add(tag)
            members.append(tag)

    memories: list[str] = []
    if mem_count > 0:
        try:
            q_vec = await embed_one(question)
            if q_vec is not None:
                memories = await repos.search_memories(asker_id, q_vec, s.memory_top_k)
        except Exception:
            log.exception("memory retrieval failed; proceeding without it")

    # Fetch relationships for this asker — memo: "Max часто катает с Игорем"
    relationships_lines: list[str] = []
    try:
        rels = await repos.relationships_for_user(chat_id, asker_id)
        for r in rels[:6]:
            kind_word = {
                "friends": "друзья с",
                "teammates": "часто катают с",
                "rivals": "соперничают с",
                "beef": "есть бифф с",
                "neutral": "нейтрально с",
            }.get(r["kind"], r["kind"])
            note = f" ({r['note']})" if r["note"] else ""
            relationships_lines.append(f"{kind_word} @{r['other_name']}{note}")
    except Exception:
        log.exception("relationships fetch failed")

    # Inject anti-repetition coaching into system prompt — previous openers
    try:
        recent = await repos.get_recent_openers(chat_id)
    except Exception:
        log.exception("failed to load openers from db")
        recent = []
    opener_note = ""
    if recent:
        opener_note = (
            "\n\nТВОИ ПОСЛЕДНИЕ ОТВЕТЫ НАЧИНАЛИСЬ С СЛОВ: "
            + ", ".join(sorted(set(recent)))
            + ". Следующий ответ ОБЯЗАТЕЛЬНО начни с другого слова. "
            "Особенно НЕ НАЧИНАЙ с 'ээ', 'ээ бой', 'ну ты', если они там есть."
        )

    rel_note = ""
    if relationships_lines:
        rel_note = "\n\nОтношения собеседника с другими: " + "; ".join(relationships_lines)

    system = build_system_prompt(
        asker_display=asker_display,
        asker_profile=summary,
        asker_traits=traits,
        memories=memories,
        chat_window=window,
        members=members,
    ) + rel_note + opener_note

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    answer = await llm.chat(messages, temperature=0.8, max_tokens=500)
    answer = _sanitize(answer)

    # If banned phrases slipped through, ask the model to rewrite.
    if _has_banned_phrase(answer):
        log.info("response contains banned phrase, regenerating: %s", answer[:80])
        rewrite_msgs = messages + [
            {"role": "assistant", "content": answer},
            {
                "role": "user",
                "content": (
                    "Перепиши свой ответ выше без слов: «десматч», «флэшка в мид», "
                    "«такси за 200к». Сохрани смысл и тон, но замени эти выражения "
                    "на другие. Только переписанный ответ, без комментариев."
                ),
            },
        ]
        try:
            answer2 = await llm.chat(rewrite_msgs, temperature=0.7, max_tokens=500)
            answer2 = _sanitize(answer2)
            if answer2 and not _has_banned_phrase(answer2):
                answer = answer2
        except Exception:
            log.exception("rewrite failed, using original")

    # Track the opener so future responses avoid repeating it (persisted)
    first = _first_word(answer)
    if first:
        try:
            await repos.push_opener(chat_id, first)
        except Exception:
            log.exception("failed to persist opener")

    return answer


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
    return await llm.chat(messages, temperature=0.3, max_tokens=600)


async def extract_and_store(chat_id: int, window_size: int | None = None) -> int:
    """Periodic job — pull recent messages, ask model to extract per-user facts,
    upsert profiles. Returns number of profiles updated.

    Semantic memories table is left empty on the free tier (no embeddings). The
    per-user `summary` and `traits` on user_profiles carry all the personality
    state the bot actually uses when answering."""
    if window_size is None:
        window_size = get_settings().memory_extract_window
    window_msgs = await repos.recent_messages(chat_id, window_size)
    if len(window_msgs) < 5:
        return 0
    window = _format_window(window_msgs, show_ids=True)
    known_ids = {m.tg_user_id for m in window_msgs if not m.is_bot}

    # Fetch existing profiles so the extractor can AUGMENT instead of OVERWRITE.
    existing_blocks: list[str] = []
    for uid in known_ids:
        summary, traits = await repos.get_profile(uid)
        if summary or traits:
            tr = json.dumps(traits, ensure_ascii=False) if traits else "{}"
            existing_blocks.append(
                f"[id={uid}]\n  summary: {summary or '(пусто)'}\n  traits: {tr}"
            )
    existing_section = "\n\n".join(existing_blocks) if existing_blocks else "(пока ничего нет)"

    user_content = (
        "ТЕКУЩИЕ ПРОФИЛИ (обязательно сохрани эти факты в новом summary, ДОБАВЛЯЙ к ним, "
        "не стирай и не перевыдумывай):\n\n"
        f"{existing_section}\n\n"
        "НОВЫЕ СООБЩЕНИЯ ЗА ОКНО (бери факты отсюда и ПРИКЛЕИВАЙ к существующим):\n\n"
        f"{window}"
    )
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    raw = await llm.chat(messages, temperature=0.2, max_tokens=1200)
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

    # Extract and persist relationships if model returned any
    rels = data.get("relationships") or []
    for r in rels:
        try:
            a = int(r.get("a"))
            b = int(r.get("b"))
            if a not in known_ids or b not in known_ids or a == b:
                continue
            kind = (r.get("kind") or "neutral").strip().lower()
            if kind not in ("friends", "teammates", "rivals", "beef", "neutral"):
                kind = "neutral"
            note = (r.get("note") or "").strip() or None
            strength = int(r.get("strength", 1))
            await repos.upsert_relationship(chat_id, a, b, kind, note, strength)
        except Exception:
            log.exception("failed to upsert relationship %s", r)

    profiles = data.get("profiles") or []
    updated = 0
    for p in profiles:
        try:
            uid = int(p["tg_user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        # Guard: model sometimes hallucinates tg_id. Only trust ids that actually
        # appeared in the window we gave it.
        if uid not in known_ids:
            log.warning("extractor returned unknown tg_id=%s, skipping", uid)
            continue
        summary = (p.get("summary") or "").strip()
        traits = p.get("traits") or {}
        mems = [m for m in (p.get("memories") or []) if isinstance(m, str) and m.strip()]

        if summary or traits:
            await repos.upsert_profile(uid, summary, traits if isinstance(traits, dict) else {})
            updated += 1
        if mems:
            try:
                vecs = await embed(mems)
                for text, vec in zip(mems, vecs):
                    await repos.add_memory(uid, text, vec, importance=1)
            except Exception:
                log.exception("failed to embed/store memories for uid=%s", uid)
    log.info("memory extraction: %d profiles updated at %s", updated, datetime.utcnow().isoformat())
    return updated


async def compact_user_memories(tg_id: int, min_count: int = 30) -> int:
    """Rewrite a user's memory list through LLM: dedupe, refine, drop stale.
    Returns final memory count (0 if skipped because too few)."""
    from app.ai.prompts import COMPACT_SYSTEM
    count = await repos.memory_count_for_user(tg_id)
    if count < min_count:
        log.info("compact skip uid=%s count=%s below threshold", tg_id, count)
        return count
    items = await repos.all_memories_for_user(tg_id)
    bullets = "\n".join(f"- {m}" for m in items)
    summary, traits = await repos.get_profile(tg_id)
    user_block = (
        f"Текущий профиль: {summary}\n"
        f"Traits: {json.dumps(traits, ensure_ascii=False)}\n\n"
        f"Все воспоминания ({len(items)} шт):\n{bullets}"
    )
    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": COMPACT_SYSTEM},
                {"role": "user", "content": user_block},
            ],
            temperature=0.2,
            max_tokens=2500,
        )
    except Exception:
        log.exception("compact LLM failed uid=%s", tg_id)
        return count

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("compact returned non-JSON uid=%s: %s", tg_id, raw[:200])
        return count

    new_mems = [m.strip() for m in (data.get("memories") or []) if isinstance(m, str) and m.strip()]
    if not new_mems:
        log.warning("compact returned empty memories uid=%s", tg_id)
        return count

    try:
        vecs = await embed(new_mems)
    except Exception:
        log.exception("compact embed failed uid=%s", tg_id)
        return count
    if len(vecs) != len(new_mems):
        log.warning("compact embed count mismatch uid=%s", tg_id)
        return count

    pairs = list(zip(new_mems, vecs))
    await repos.replace_memories(tg_id, pairs)
    log.info("compact done uid=%s: %d -> %d", tg_id, count, len(new_mems))
    return len(new_mems)


async def compact_all_users() -> dict:
    ids = await repos.distinct_user_ids_with_memories()
    result = {"processed": 0, "total": len(ids), "details": []}
    for uid in ids:
        try:
            new_count = await compact_user_memories(uid)
            result["processed"] += 1
            result["details"].append({"uid": uid, "new_count": new_count})
        except Exception:
            log.exception("compact_all_users failed for uid=%s", uid)
    return result
