"""Question Engine — бот сам спрашивает чтобы заполнить пробелы в профиле.

Вместо рудиментарной "1 вопрос на 5 сообщений" из старого промпта — здесь:
- Profile Completion Score: считаем какие филды профиля известны
- Отбираем наименее заполненный + НЕ задаваемый ранее
- Формулируем вопрос в стиле текущего модуса (chill / otmoroz / hype / ...)
- Лимит 2 вопроса в неделю на юзера (анти-надоедливость)
- Сохраняем pending_questions(user_id, question, field, asked_at, resolved_at)
- На следующее сообщение юзера экстрактор подхватит ответ и обновит профиль
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.ai import personas
from app.db import repos

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# Profile fields — что мы хотим знать о каждом игроке
# ════════════════════════════════════════════════════════════════
# (key, weight, description) — weight используется для приоритета при выборе
PROFILE_FIELDS: list[tuple[str, int, str]] = [
    ("age",       3, "возраст"),
    ("city",      3, "город / откуда"),
    ("job",       3, "работа / учёба"),
    ("hobbies",   2, "хобби и интересы"),
    ("family",    2, "семья (девушка / жена / дети)"),
    ("fav_games", 2, "любимые игры"),
    ("music",     2, "музыка"),
    ("movies",    1, "фильмы / сериалы"),
    ("irl",       1, "что в реале происходит"),
    ("vibe",      1, "общий характер / манера"),
]

# Максимум вопросов в неделю на одного юзера (анти-надоедливость)
MAX_QUESTIONS_PER_WEEK = 2

# Cooldown — не задаём вопрос юзеру если последний был в последние N часов
COOLDOWN_HOURS = 48


# ════════════════════════════════════════════════════════════════
# Profile Completion Score
# ════════════════════════════════════════════════════════════════
def completion_score(traits: dict) -> tuple[int, list[str]]:
    """Возвращает (0..100 score, список незаполненных филдов).

    100 = всё известно, 0 = ничего.
    """
    if not traits:
        return 0, [k for k, _, _ in PROFILE_FIELDS]

    total_weight = sum(w for _, w, _ in PROFILE_FIELDS)
    filled_weight = 0
    missing: list[str] = []

    for key, weight, _ in PROFILE_FIELDS:
        v = traits.get(key)
        # filled если есть строка/число/непустой список
        if v is None or v == "" or v == [] or v == {}:
            missing.append(key)
        else:
            filled_weight += weight

    pct = int((filled_weight / total_weight) * 100)
    return pct, missing


# ════════════════════════════════════════════════════════════════
# Question generation
# ════════════════════════════════════════════════════════════════
@dataclass
class QuestionTemplate:
    """Шаблон вопроса, варьируется по persona и filed."""
    field: str
    chill: list[str]
    otmoroz: list[str]
    hype: list[str]


# Шаблоны вопросов под каждый филд × модус
QUESTION_TEMPLATES = {
    "age": QuestionTemplate(
        field="age",
        chill=[
            "ты сам-то какого года, если не секрет?",
            "слушай, тебе сколько? просто интересно как звучишь",
            "тебе сколько лет вообще?",
        ],
        otmoroz=[
            "тебе хоть сколько лет, аоау?",
            "сколько тебе, чтоб такое говорить?",
        ],
        hype=[
            "СКОЛЬКО ТЕБЕ ЛЕТ БРАТ 🔥",
            "тебе скока лет вообще, интересно",
        ],
    ),
    "city": QuestionTemplate(
        field="city",
        chill=[
            "ты сам-то откуда вообще?",
            "из какого города пишешь?",
            "ты в каком регионе обитаешь?",
        ],
        otmoroz=[
            "откуда ты вообще?",
        ],
        hype=[
            "ОТКУДА ТЫ?? 🌐",
        ],
    ),
    "job": QuestionTemplate(
        field="job",
        chill=[
            "а ты вообще работаешь или учишься, чем днём занят?",
            "кем работаешь / чем занимаешься по жизни?",
            "ты какой деятельностью занят вне чата?",
        ],
        otmoroz=[
            "ты чем вообще занимаешься в жизни?",
        ],
        hype=[
            "БРАТАН ЧЕМ ЗАНЯТ ПО ЖИЗНИ?",
        ],
    ),
    "hobbies": QuestionTemplate(
        field="hobbies",
        chill=[
            "что вне игр любишь — хобби какое-нибудь, спорт, ещё чего?",
            "чем отдыхаешь когда не за компом?",
        ],
        otmoroz=[
            "у тебя кроме чата вообще жизнь есть? хобби какие?",
        ],
        hype=[
            "ЧТО ЛЮБИШЬ КРОМЕ ЭТОГО ЧАТА 🎯",
        ],
    ),
    "family": QuestionTemplate(
        field="family",
        chill=[
            "девушка / жена есть, если не секрет?",
            "ты женат или одинокий волк?",
        ],
        otmoroz=[
            "тебя дома ждут или один сидишь?",
        ],
        hype=[
            "СЕМЬЯ / ОДИНОКИЙ? 💍",
        ],
    ),
    "fav_games": QuestionTemplate(
        field="fav_games",
        chill=[
            "во что ещё кроме катки гоняешь?",
            "какая у тебя любимая игра вообще?",
        ],
        otmoroz=[
            "во что играешь кроме того где сливаешь?",
        ],
        hype=[
            "ЧТО ИГРАЕШЬ ЛЮБИМОЕ?",
        ],
    ),
    "music": QuestionTemplate(
        field="music",
        chill=[
            "ты под что катаешь обычно — музыкальный плейлист какой?",
            "что в плеере крутится в основном?",
        ],
        otmoroz=[
            "что слушаешь, чтоб такое выдавать?",
        ],
        hype=[
            "МУЗЫКА КАКАЯ В ПЛЕЕРЕ 🎧",
        ],
    ),
    "movies": QuestionTemplate(
        field="movies",
        chill=[
            "сериал какой смотрел недавно нормальный?",
            "из фильмов / сериалов чё топ за последний год?",
        ],
        otmoroz=[
            "что смотришь когда не пишешь хуйни в чат?",
        ],
        hype=[
            "ЧТО СМОТРЕЛ НОРМ ПОСЛЕДНЕЕ? 🎬",
        ],
    ),
    "irl": QuestionTemplate(
        field="irl",
        chill=[
            "как у тебя по жизни вообще сейчас? нормально?",
            "по реалу как обстановка?",
        ],
        otmoroz=[
            "как у тебя вообще, не сидишь же ты только в чате",
        ],
        hype=[
            "КАК ВООБЩЕ ПО ЖИЗНИ БРАТ?",
        ],
    ),
    "vibe": QuestionTemplate(
        field="vibe",
        chill=[
            "ты вообще себя как чел опишешь — общительный, интроверт, ещё какой?",
        ],
        otmoroz=[
            "ты сам-то как себя описываешь, в двух словах?",
        ],
        hype=[
            "ТЫ ВООБЩЕ КАКОЙ ПО ХАРАКТЕРУ?",
        ],
    ),
}


def _pick_template_variant(template: QuestionTemplate, persona_key: str) -> str:
    """Возвращает один вариант формулировки под текущий модус."""
    bank = {
        "chill":   template.chill,
        "otmoroz": template.otmoroz,
        "hype":    template.hype,
        "filosof": template.chill,    # философ говорит спокойно
        "obizhen": template.chill,    # обиделся — тоже спокойно (вряд ли вообще задаст)
    }.get(persona_key, template.chill)
    return random.choice(bank)


def select_field_to_ask(missing: list[str], asked_recently: list[str]) -> str | None:
    """Выбирает следующий филд для вопроса.

    Приоритет — из missing исключаем уже спрошенные недавно, дальше выбираем
    рандомно с учётом веса.
    """
    candidates: list[tuple[str, int]] = []
    for key, weight, _ in PROFILE_FIELDS:
        if key not in missing:
            continue
        if key in asked_recently:
            continue
        candidates.append((key, weight))

    if not candidates:
        return None

    # Weighted random pick
    total_weight = sum(w for _, w in candidates)
    r = random.randint(1, total_weight)
    upto = 0
    for key, weight in candidates:
        upto += weight
        if upto >= r:
            return key
    return candidates[0][0]


# ════════════════════════════════════════════════════════════════
# Main entry — should we ask?
# ════════════════════════════════════════════════════════════════
async def maybe_generate_question(
    user_id: int,
    traits: dict,
    persona_key: str = "chill",
) -> str | None:
    """Решает — задавать ли вопрос юзеру сейчас.

    Возвращает текст вопроса для добавления в конец ответа бота.
    None — если нет смысла спрашивать (профиль заполнен / лимит / cooldown).
    """
    # 1. Профиль уже почти заполнен → не пристаём
    score, missing = completion_score(traits or {})
    if score >= 80:
        return None

    # 2. Cooldown / weekly limit
    try:
        recent_count = await repos.questions_asked_in_window(user_id, hours=24 * 7)
        last_at = await repos.last_question_asked_at(user_id)
    except Exception:
        log.exception("question_engine: failed to query history")
        return None

    if recent_count >= MAX_QUESTIONS_PER_WEEK:
        return None

    if last_at is not None:
        elapsed = datetime.now(timezone.utc) - last_at
        if elapsed < timedelta(hours=COOLDOWN_HOURS):
            return None

    # 3. Какие филды уже спрашивали недавно — пропускаем
    try:
        asked_recently = await repos.fields_asked_recently(user_id, days=30)
    except Exception:
        asked_recently = []

    # 4. Выбираем филд + формулировку
    field = select_field_to_ask(missing, asked_recently)
    if field is None:
        return None

    template = QUESTION_TEMPLATES.get(field)
    if template is None:
        return None

    question = _pick_template_variant(template, persona_key)

    # 5. Сохраняем pending_question
    try:
        await repos.add_pending_question(user_id, field, question)
    except Exception:
        log.exception("question_engine: failed to persist pending_question")
        return None

    log.info(
        "question_engine: asking user_id=%s field=%s persona=%s score=%d%% (%d/%d this week)",
        user_id, field, persona_key, score,
        recent_count + 1, MAX_QUESTIONS_PER_WEEK,
    )
    return question
