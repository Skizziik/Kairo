from __future__ import annotations

import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.ai import llm
from app.services import search

router = Router(name="google")
log = logging.getLogger(__name__)

# Natural-language trigger: "загугли X", "погугли X", "гугли X", "поищи X"
TRIGGER = re.compile(
    r"^\s*(?:за|по)?гугли|^\s*поищ[ьи]|^\s*загугл[иь]|^\s*найди\s+в\s+нете",
    re.IGNORECASE,
)


def _extract_query_from_trigger(text: str) -> str:
    """Strip the trigger word and return the actual query."""
    # cheap heuristic: drop first word plus possible punctuation
    words = text.strip().split(maxsplit=1)
    if len(words) < 2:
        return ""
    return words[1].lstrip(",.:—- ").strip()


def _format_results(query: str, hits: list[search.SearchHit]) -> str:
    """Build a compact message with up to 5 results + clickable titles."""
    if not hits:
        return f"<b>Не нашёл по запросу:</b> {escape(query)}"
    lines = [f"🔎 <b>{escape(query)}</b>"]
    for i, h in enumerate(hits[:5], 1):
        title = escape(h.title or h.url or "—")
        if h.url:
            lines.append(f"\n{i}. <a href=\"{escape(h.url, quote=True)}\">{title}</a>")
        else:
            lines.append(f"\n{i}. {title}")
        if h.snippet:
            snippet = h.snippet
            if len(snippet) > 220:
                snippet = snippet[:220].rsplit(" ", 1)[0] + "…"
            lines.append(escape(snippet))
    return "".join(lines)


async def _summarize_for_bot(query: str, hits: list[search.SearchHit]) -> str:
    """Ask LLM to give a short RIP-style summary of the search results."""
    joined = "\n\n".join(
        f"[{i}] {h.title}\n{h.url}\n{h.snippet}"
        for i, h in enumerate(hits[:5], 1)
    )
    system = (
        "Ты RIP нагибатор — коротко пересказываешь результаты поиска пацанам в чате. "
        "Формат: 1-3 предложения сути, без воды, в своей манере (ирония допустима если уместна). "
        "НЕ выдумывай факты которых нет в результатах. Если инфа противоречивая — скажи."
    )
    user = f"Запрос: {query}\n\nРезультаты поиска:\n\n{joined}\n\nДай короткую сводку."
    try:
        return await llm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.5,
            max_tokens=400,
        )
    except Exception:
        log.exception("search summary LLM failed")
        return ""


async def _run_search(msg: Message, query: str) -> None:
    if not query:
        await msg.reply("Чего гуглить-то? Пиши запрос рядом с командой.")
        return
    await msg.bot.send_chat_action(msg.chat.id, "typing")
    hits = await search.web_search(query, limit=5)
    if not hits:
        await msg.reply("DuckDuckGo не отдал ничего. Попробуй переформулировать.")
        return

    summary = await _summarize_for_bot(query, hits)
    links = _format_results(query, hits)
    if summary:
        text = f"{summary}\n\n{links}"
    else:
        text = links
    await msg.reply(text, disable_web_page_preview=True)


@router.message(Command("google", "gugle", "g"))
async def cmd_google(msg: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query and msg.reply_to_message:
        query = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    await _run_search(msg, query)


@router.message(F.text.regexp(TRIGGER))
async def natural_trigger(msg: Message) -> None:
    if msg.from_user is None or msg.from_user.is_bot:
        return
    text = msg.text or ""
    query = _extract_query_from_trigger(text)
    if not query:
        return
    await _run_search(msg, query)
