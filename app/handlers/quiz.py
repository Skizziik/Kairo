from __future__ import annotations

import logging
import random

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, PollAnswer

from app.economy import repo as eco

router = Router(name="quiz")
log = logging.getLogger(__name__)


# Каждый вопрос: (question, [options], correct_index, explanation)
QUESTIONS: list[tuple[str, list[str], int, str]] = [
    ("Сколько раундов нужно для победы в CS2 competitive (премьер)?",
     ["13", "16", "24", "30"], 0,
     "В CS2 премьер — MR12, первым до 13 побед, с овертаймами по необходимости."),
    ("Что означает callout «Pit» на Mirage?",
     ["T-spawn", "A site, нижняя позиция снизу от A main", "B apartments", "Mid window"], 1,
     "Pit — маленький спот на A, рядом с default plant, справа от A main."),
    ("Сколько стоит AWP в CS2?",
     ["$4200", "$4750", "$5000", "$5500"], 1,
     "AWP стоит $4750 — дороже всей остальной винтовочной линейки."),
    ("Какая карта НЕ входит в active duty пул 2026?",
     ["Mirage", "Inferno", "Cache", "Ancient"], 2,
     "Cache выкинули из пула давно, сейчас в active duty — Mirage, Inferno, Nuke, Ancient, Anubis, Dust2, Train."),
    ("Что такое «eco-round»?",
     ["Раунд когда всё покупают", "Раунд когда все сохраняют деньги", "Раунд с force buy", "Pistol round"], 1,
     "Eco — раунд сохранения денег, покупают минимум, готовят экономику под следующий full-buy."),
    ("Сколько времени после плана C4 бомба взрывается?",
     ["30 сек", "35 сек", "40 сек", "45 сек"], 2,
     "40 секунд с момента установки до взрыва."),
    ("Какой про-игрок считается лучшим AWP-ером последнего десятилетия?",
     ["s1mple", "ZywOo", "kennyS", "все трое в разные периоды"], 3,
     "Это спор вечный — s1mple, ZywOo и kennyS доминировали в разные эры."),
    ("Что означает «1v5 clutch»?",
     ["5 игроков против 1", "Один выжил и победил 5 противников", "Раунд с 5 kill'ами", "AWP фраг"], 1,
     "Clutch 1v5 — один игрок убивает всю команду противника. Редкое событие, попадает в HLTV highlights."),
    ("Какой оператор используется для создания смок-гранаты в игре?",
     ["HE Grenade", "Smoke Grenade", "Flashbang", "Incendiary"], 1,
     "Smoke grenade — создаёт дымовую стену на ~18 секунд."),
    ("Что такое «pre-aim»?",
     ["Выстрел до противника", "Держание прицела на месте где ожидается враг", "Быстрая смена оружия", "Стрейф"], 1,
     "Pre-aim — держать прицел на высоте головы в точке где противник должен появиться. Экономит долю секунды."),
    ("Как называется самая дорогая стикер-капсула в кс (2026)?",
     ["Katowice 2014", "Boston 2018", "Cologne 2016", "Stockholm 2021"], 0,
     "Katowice 2014 — легендарная капсула, стикеры оттуда самые дорогие в игре."),
    ("Что означает «peek» в CS-терминологии?",
     ["Выстрел из-за угла", "Быстрый выход на позицию чтобы увидеть/убить врага", "Флеш-бросок", "Запрыг на бокс"], 1,
     "Peek — выход на позицию чтобы глянуть/выстрелить. Бывает jiggle peek, wide peek, shoulder peek."),
    ("Какая команда выиграла последний мейджор?",
     ["Natus Vincere", "FaZe Clan", "Team Spirit", "Vitality"], 2,
     "Team Spirit — одна из топовых команд последних лет, обыгрывали всех в финалах."),
]


@router.message(Command("quiz"))
async def cmd_quiz(msg: Message) -> None:
    q, opts, correct_idx, explanation = random.choice(QUESTIONS)
    try:
        sent = await msg.bot.send_poll(
            chat_id=msg.chat.id,
            question=f"🎯 {q}  (+20 🪙 за верный ответ)",
            options=opts,
            type="quiz",
            correct_option_id=correct_idx,
            explanation=explanation[:200],
            is_anonymous=False,
        )
        # Register poll for coin rewards
        if sent.poll:
            await eco.register_quiz(sent.poll.id, correct_idx, reward=20)
    except Exception as e:
        log.exception("quiz failed")
        await msg.reply(f"Не смог: {e}")


@router.poll_answer()
async def on_poll_answer(answer: PollAnswer) -> None:
    if answer.user is None:
        return
    try:
        result = await eco.handle_quiz_answer(
            answer.poll_id,
            answer.user.id,
            list(answer.option_ids or []),
        )
    except Exception:
        log.exception("quiz answer handling failed")
        return
    # We don't reply in chat — reward is silent; next /balance will show growth
    if result.get("rewarded"):
        log.info("quiz reward +%s to %s", result.get("reward"), answer.user.id)
