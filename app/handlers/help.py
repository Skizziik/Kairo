from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="help")

TEXT = (
    "<b>RIP нагибатор — мануал</b>\n\n"
    "<b>Общение:</b>\n"
    "/ai &lt;вопрос&gt; — спросить меня. Можно реплаем, @упоминанием или просто назвать меня по имени.\n"
    "/tldr — пересказ последних сообщений в чате.\n\n"
    "<b>CS2-фичи:</b>\n"
    "/lfg — собрать 5-стак. Жми «Я в деле».\n"
    "/stats &lt;steamid&gt; — статы CS2.\n"
    "/inv &lt;steamid&gt; — инвентарь.\n"
    "/map — случайная карта. /map pool — весь пул.\n"
    "/yt &lt;url&gt; — скачать трек с YouTube.\n\n"
    "<b>Память:</b>\n"
    "/me — твой профиль и что я о тебе помню.\n"
    "/profile @ник или реплаем — что я знаю о ком угодно.\n"
    "/forget — стереть мою память о тебе.\n"
    "/top — топ активных в чате.\n\n"
    "<b>Админское:</b>\n"
    "/extract — форс-апдейт профилей по свежему чату.\n"
    "/teach @ник &lt;факт&gt; — вручную запомнить факт.\n\n"
    "<i>Я вижу чат и учусь по ходу. Иногда сам вкидываю коммент.</i>"
)


@router.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(TEXT)
