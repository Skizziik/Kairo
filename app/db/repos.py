from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.db.client import pool


@dataclass
class UserRow:
    tg_id: int
    username: str | None
    first_name: str | None
    last_name: str | None


@dataclass
class MessageRow:
    tg_user_id: int
    username: str | None
    first_name: str | None
    text: str
    is_bot: bool
    created_at: datetime


async def upsert_user(
    tg_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into users (tg_id, username, first_name, last_name) values ($1,$2,$3,$4) "
            "on conflict (tg_id) do update set username=excluded.username, "
            "first_name=excluded.first_name, last_name=excluded.last_name, seen_at=now()",
            tg_id, username, first_name, last_name,
        )
        await conn.execute(
            "insert into user_profiles (tg_id) values ($1) on conflict (tg_id) do nothing",
            tg_id,
        )


async def log_message(
    chat_id: int,
    tg_user_id: int,
    text: str,
    reply_to: int | None = None,
    is_bot: bool = False,
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into messages (chat_id, tg_user_id, reply_to, text, is_bot) "
            "values ($1, $2, $3, $4, $5)",
            chat_id, tg_user_id, reply_to, text, is_bot,
        )


async def recent_messages(chat_id: int, limit: int) -> list[MessageRow]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select m.tg_user_id, u.username, u.first_name, m.text, m.is_bot, m.created_at
            from messages m
            left join users u on u.tg_id = m.tg_user_id
            where m.chat_id = $1
            order by m.created_at desc
            limit $2
            """,
            chat_id, limit,
        )
    return [
        MessageRow(
            tg_user_id=r["tg_user_id"],
            username=r["username"],
            first_name=r["first_name"],
            text=r["text"],
            is_bot=r["is_bot"],
            created_at=r["created_at"],
        )
        for r in reversed(rows)
    ]


async def get_profile(tg_id: int) -> tuple[str, dict]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select summary, traits from user_profiles where tg_id = $1",
            tg_id,
        )
    if row is None:
        return "", {}
    traits = row["traits"] if isinstance(row["traits"], dict) else json.loads(row["traits"] or "{}")
    return row["summary"] or "", traits


async def get_profile_full(tg_id: int) -> dict | None:
    """Returns full profile with metadata or None if not found."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            select p.summary, p.traits, p.updated_at, u.username, u.first_name
            from user_profiles p
            left join users u on u.tg_id = p.tg_id
            where p.tg_id = $1
            """,
            tg_id,
        )
    if row is None:
        return None
    traits = row["traits"] if isinstance(row["traits"], dict) else json.loads(row["traits"] or "{}")
    return {
        "summary": row["summary"] or "",
        "traits": traits,
        "updated_at": row["updated_at"],
        "username": row["username"],
        "first_name": row["first_name"],
    }


async def find_user_by_username(username: str) -> int | None:
    username = username.lstrip("@").lower()
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select tg_id from users where lower(username) = $1",
            username,
        )
    return row["tg_id"] if row else None


async def recent_memories(user_id: int, limit: int = 10) -> list[tuple[str, int, object]]:
    """Return (content, importance, created_at) ordered by newest first."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select content, importance, created_at
            from memories
            where user_id = $1
            order by created_at desc
            limit $2
            """,
            user_id, limit,
        )
    return [(r["content"], int(r["importance"]), r["created_at"]) for r in rows]


async def wipe_user_data(tg_id: int) -> dict:
    """Delete memories + reset profile. Keep user + messages (chat log stays)."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            deleted_mem = await conn.fetchval(
                "with d as (delete from memories where user_id = $1 returning 1) "
                "select count(*) from d",
                tg_id,
            )
            await conn.execute(
                "update user_profiles set summary='', traits='{}'::jsonb, updated_at=now() where tg_id = $1",
                tg_id,
            )
    return {"memories_deleted": int(deleted_mem or 0)}


async def upsert_profile(tg_id: int, summary: str, traits: dict) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into user_profiles (tg_id, summary, traits, updated_at)
            values ($1, $2, $3::jsonb, now())
            on conflict (tg_id) do update set
                summary = excluded.summary,
                traits = excluded.traits,
                updated_at = now()
            """,
            tg_id, summary, json.dumps(traits),
        )


async def all_memories_for_user(user_id: int) -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select content from memories where user_id = $1 order by created_at desc",
            user_id,
        )
    return [r["content"] for r in rows]


async def memory_count_for_user(user_id: int) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "select count(*) from memories where user_id = $1",
            user_id,
        ) or 0)


async def replace_memories(user_id: int, new_memories: list[tuple[str, list[float]]]) -> None:
    """Wipe all existing memories for user and insert new compacted set atomically."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("delete from memories where user_id = $1", user_id)
            for content, embedding in new_memories:
                await conn.execute(
                    "insert into memories (user_id, content, embedding, importance) "
                    "values ($1, $2, $3, 2)",
                    user_id, content, embedding,
                )


async def distinct_user_ids_with_memories() -> list[int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("select distinct user_id from memories")
    return [int(r["user_id"]) for r in rows]


async def add_memory(user_id: int, content: str, embedding: list[float], importance: int = 1) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into memories (user_id, content, embedding, importance) values ($1, $2, $3, $4)",
            user_id, content, embedding, importance,
        )


async def search_memories(user_id: int, query_embedding: list[float], k: int) -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select content
            from memories
            where user_id = $1
            order by embedding <=> $2
            limit $3
            """,
            user_id, query_embedding, k,
        )
    return [r["content"] for r in rows]


async def get_recent_openers(chat_id: int) -> list[str]:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select recent_openers from bot_chat_state where chat_id = $1",
            chat_id,
        )
    if row is None:
        return []
    val = row["recent_openers"]
    if isinstance(val, list):
        return [str(x) for x in val]
    try:
        return list(json.loads(val or "[]"))
    except Exception:
        return []


async def push_opener(chat_id: int, word: str, max_keep: int = 10) -> None:
    if not word:
        return
    existing = await get_recent_openers(chat_id)
    existing.append(word)
    if len(existing) > max_keep:
        existing = existing[-max_keep:]
    payload = json.dumps(existing, ensure_ascii=False)
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into bot_chat_state (chat_id, recent_openers, updated_at)
            values ($1, $2::jsonb, now())
            on conflict (chat_id) do update set
                recent_openers = excluded.recent_openers,
                updated_at = now()
            """,
            chat_id, payload,
        )


async def can_chime_and_mark(chat_id: int, cooldown_seconds: int) -> bool:
    """Atomic: check cooldown; if expired, mark and return True. Else False."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select last_chime_at from bot_chat_state where chat_id = $1 for update",
                chat_id,
            )
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            if row is not None and row["last_chime_at"] is not None:
                if (now - row["last_chime_at"]).total_seconds() < cooldown_seconds:
                    return False
            await conn.execute(
                """
                insert into bot_chat_state (chat_id, last_chime_at, updated_at)
                values ($1, $2, now())
                on conflict (chat_id) do update set
                    last_chime_at = excluded.last_chime_at,
                    updated_at = now()
                """,
                chat_id, now,
            )
            return True


async def bump_extract_counter(step: int = 1) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "update kv_state set v = v + $1 where k = 'msgs_since_extract' returning v",
            step,
        )
    return int(row["v"]) if row else 0


async def reset_extract_counter() -> None:
    async with pool().acquire() as conn:
        await conn.execute("update kv_state set v = 0 where k = 'msgs_since_extract'")


async def add_warn(tg_user_id: int, chat_id: int, reason: str | None, issued_by: int) -> int:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into warns (tg_user_id, chat_id, reason, issued_by)
            values ($1, $2, $3, $4) returning id
            """,
            tg_user_id, chat_id, reason, issued_by,
        )
        count = await conn.fetchval(
            "select count(*) from warns where tg_user_id = $1 and chat_id = $2",
            tg_user_id, chat_id,
        )
    return int(count)


async def chat_members_seen(chat_id: int, days: int = 60) -> list[UserRow]:
    """Return distinct users who posted in this chat within last N days."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select distinct on (u.tg_id) u.tg_id, u.username, u.first_name, u.last_name
            from messages m
            join users u on u.tg_id = m.tg_user_id
            where m.chat_id = $1
              and m.created_at > now() - interval '{int(days)} days'
              and m.is_bot = false
            """,
            chat_id,
        )
    return [
        UserRow(
            tg_id=r["tg_id"],
            username=r["username"],
            first_name=r["first_name"],
            last_name=r["last_name"],
        )
        for r in rows
    ]


async def random_memory_for_chat(chat_id: int) -> tuple[int, str, str | None] | None:
    """Pick a random recent memory of any user who posts in this chat.
    Returns (user_id, content, username) or None if no memories exist."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            select m.user_id, m.content, u.username
            from memories m
            join users u on u.tg_id = m.user_id
            where m.user_id in (
                select distinct tg_user_id from messages
                where chat_id = $1 and is_bot = false
                and created_at > now() - interval '30 days'
            )
            order by random()
            limit 1
            """,
            chat_id,
        )
    return (int(row["user_id"]), row["content"], row["username"]) if row else None


async def messages_since_hours(chat_id: int, hours: int) -> list[MessageRow]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select m.tg_user_id, u.username, u.first_name, m.text, m.is_bot, m.created_at
            from messages m
            left join users u on u.tg_id = m.tg_user_id
            where m.chat_id = $1
              and m.created_at > now() - (interval '1 hour' * $2)
            order by m.created_at asc
            """,
            chat_id, hours,
        )
    return [
        MessageRow(
            tg_user_id=r["tg_user_id"],
            username=r["username"],
            first_name=r["first_name"],
            text=r["text"],
            is_bot=r["is_bot"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


async def upsert_relationship(
    chat_id: int,
    user_a: int,
    user_b: int,
    kind: str,
    note: str | None,
    strength: int,
) -> None:
    if user_a == user_b:
        return
    a, b = (user_a, user_b) if user_a < user_b else (user_b, user_a)
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into relationships (chat_id, user_a, user_b, kind, note, strength, updated_at)
            values ($1, $2, $3, $4, $5, $6, now())
            on conflict (chat_id, user_a, user_b) do update set
                kind = excluded.kind,
                note = coalesce(excluded.note, relationships.note),
                strength = greatest(relationships.strength, excluded.strength),
                updated_at = now()
            """,
            chat_id, a, b, kind, note, max(1, min(5, int(strength))),
        )


async def relationships_for_user(chat_id: int, tg_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select r.user_a, r.user_b, r.kind, r.note, r.strength,
                   ua.username as ua_username, ua.first_name as ua_first,
                   ub.username as ub_username, ub.first_name as ub_first
            from relationships r
            left join users ua on ua.tg_id = r.user_a
            left join users ub on ub.tg_id = r.user_b
            where r.chat_id = $1 and ($2 in (r.user_a, r.user_b))
            order by r.strength desc, r.updated_at desc
            """,
            chat_id, tg_id,
        )
    out = []
    for r in rows:
        other_id = r["user_b"] if r["user_a"] == tg_id else r["user_a"]
        other_name = (
            r["ub_username"] if r["user_a"] == tg_id else r["ua_username"]
        ) or (
            r["ub_first"] if r["user_a"] == tg_id else r["ua_first"]
        ) or f"user{other_id}"
        out.append({
            "other_id": other_id,
            "other_name": other_name,
            "kind": r["kind"],
            "note": r["note"],
            "strength": int(r["strength"]),
        })
    return out


async def log_bot_message(chat_id: int, message_id: int, text: str) -> None:
    if not text.strip():
        return
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into bot_messages (chat_id, message_id, text) values ($1, $2, $3) "
            "on conflict (chat_id, message_id) do nothing",
            chat_id, message_id, text[:4000],
        )


async def record_reaction(chat_id: int, message_id: int, emoji: str, user_id: int) -> bool:
    """Set reaction on a bot message if it was a bot message. Returns True if recorded."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "update bot_messages set reaction=$3, reaction_by=$4, reaction_at=now() "
            "where chat_id=$1 and message_id=$2 and reaction is null "
            "returning id",
            chat_id, message_id, emoji, user_id,
        )
    return row is not None


async def feedback_digest(chat_id: int, limit: int = 20) -> dict:
    """For recent bot messages, summarize feedback reception: with which emojis."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select text, reaction, created_at from bot_messages "
            "where chat_id = $1 and created_at > now() - interval '14 days' "
            "order by created_at desc limit $2",
            chat_id, limit,
        )
    hits = [(r["text"], r["reaction"]) for r in rows]
    positive = sum(1 for _, r in hits if r in POSITIVE_EMOJIS)
    negative = sum(1 for _, r in hits if r in NEGATIVE_EMOJIS)
    return {
        "total": len(hits),
        "positive": positive,
        "negative": negative,
        "recent": hits[:5],
    }


POSITIVE_EMOJIS = {"🔥", "😁", "👍", "💯", "❤", "❤️", "🫡", "🤝", "😂", "🤣", "👏"}
NEGATIVE_EMOJIS = {"💩", "🤡", "👎", "🤮", "🖕"}


async def top_active(chat_id: int, days: int = 7, limit: int = 10) -> list[tuple[str, int]]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select coalesce(u.username, u.first_name, 'anon') as name, count(*) as n
            from messages m
            left join users u on u.tg_id = m.tg_user_id
            where m.chat_id = $1
              and m.created_at > now() - interval '{int(days)} days'
              and m.is_bot = false
            group by 1
            order by n desc
            limit $2
            """,
            chat_id, limit,
        )
    return [(r["name"], int(r["n"])) for r in rows]
