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
