from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.config import get_settings


class AntispamMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        s = get_settings()
        if event.from_user is None or event.from_user.is_bot:
            return await handler(event, data)

        now = time.time()
        user_id = event.from_user.id
        q = self._hits[user_id]
        cutoff = now - 60
        while q and q[0] < cutoff:
            q.popleft()
        q.append(now)
        if len(q) > s.rate_limit_per_minute and user_id not in s.admin_id_set:
            # drop silently — don't feed flooder any dopamine
            return
        return await handler(event, data)
