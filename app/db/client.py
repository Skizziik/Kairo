from __future__ import annotations

import logging

import asyncpg
from pgvector.asyncpg import register_vector

from app.config import get_settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    s = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=s.database_url,
        min_size=1,
        max_size=5,
        init=_init_connection,
        # Supabase pooler (pgbouncer transaction mode) breaks prepared statements
        statement_cache_size=0,
    )
    log.info("asyncpg pool ready")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool is not initialised")
    return _pool
