from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="help")

TEXT = (
    "<b>RIP нагибатор — мануал</b>\n\n"
    "/lfg — собрать 5-стак. Жми кнопку «Я в деле» и погнали.\n"
    "/ai &lt;вопрос&gt; — спросить меня (или просто реплайни на моё сообщение).\n"
    "/tldr — пересказ последних сообщений в чате.\n"
    "/stats &lt;steamid&gt; — статы CS2 по Steam ID.\n"
    "/inv &lt;steamid&gt; — стоимость инвентаря по Steam ID.\n"
    "/map — случайная карта из активного пула. /map pool — весь пул.\n"
    "/yt &lt;url&gt; — скачать трек с YouTube.\n"
    "/me — мой профиль и что я о тебе помню.\n"
    "/top — топ активных в чате за неделю.\n\n"
    "<i>Я вижу чат и помню кто ты. Не бойся обращаться по-человечески.</i>"
)


@router.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(TEXT)
