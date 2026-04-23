from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="help")

TEXT = (
    "<b>RIP нагибатор — мануал</b>\n\n"
    "<b>Общение:</b>\n"
    "/ai &lt;вопрос&gt; — спросить меня. Реплай, @упоминание, по имени — всё ок.\n"
    "/tldr — пересказ последних сообщений.\n\n"
    "<b>CS2:</b>\n"
    "/lfg — собрать 5-стак (тегаю всех).\n"
    "/stats &lt;steamid&gt; — статы CS2.\n"
    "/inv &lt;steamid&gt; — инвентарь.\n"
    "/map — рандом карта. /map pool — весь пул.\n"
    "/nade &lt;карта&gt; — раскидка (smoke/flash/molly). Пример: /nade mirage.\n\n"
    "<b>Мультимедиа:</b>\n"
    "/yt &lt;url&gt; — трек с YouTube.\n"
    "/google &lt;запрос&gt; — загуглить. Также «загугли X» без слеша.\n"
    "<i>Картинки и голосовые я вижу и слышу — кидай смело.</i>\n\n"
    "<b>Память:</b>\n"
    "/me — твой профиль.\n"
    "/profile @ник или реплаем — что я знаю о ком угодно.\n"
    "/forget — стереть мою память о тебе.\n"
    "/top — топ активных.\n\n"
    "<b>Игрушки:</b>\n"
    "/roll [N] — рандом от 1 до N (100 дефолт).\n"
    "/match или /1v1 реплаем — рандом битва 1v1.\n"
    "/timer 5m [текст] — напоминалка.\n"
    "/poll вопрос | 1 | 2 | 3 — опрос.\n"
    "/quiz — CS2 викторина.\n\n"
    "<b>Админское:</b>\n"
    "/warn /mute /ban /unmute /unban /warns — модерация.\n"
    "/extract /compact — форс-апдейт памяти.\n"
    "/teach @ник &lt;факт&gt; — ручное обучение.\n"
)


@router.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(TEXT)
