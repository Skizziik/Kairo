"""Mood Engine — динамическое настроение бота.

Хранит per-chat состояние:
  • mood:      -100..100  (грустный..радостный)
  • energy:    0..100     (вялый..энергичный, привязка к времени суток)
  • offended:  0..100     (нет..сильно обижен)
  • toxicity:  0..100     (милый..злой)

Каждое сообщение в чате (даже не адресованное боту) тригерит апдейт состояния.
Когда бот собирается ответить — селектор выбирает persona по правилам.

Сериализуется как JSON в bot_chat_state.mood_state (jsonb).
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.ai import personas

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# State container
# ════════════════════════════════════════════════════════════════
@dataclass
class MoodState:
    mood:        int = 0
    energy:      int = 50
    offended:    int = 0
    toxicity:    int = 30
    last_persona: str = "chill"
    last_updated_iso: str = ""        # ISO timestamp последнего апдейта
    day_seed:    int = 0              # рандомный сдвиг за день (-30..+30)
    day_seed_date: str = ""           # YYYY-MM-DD когда сгенерили seed

    @classmethod
    def from_jsonb(cls, raw: Optional[dict]) -> "MoodState":
        if not raw:
            return cls()
        try:
            return cls(
                mood=int(raw.get("mood", 0)),
                energy=int(raw.get("energy", 50)),
                offended=int(raw.get("offended", 0)),
                toxicity=int(raw.get("toxicity", 30)),
                last_persona=str(raw.get("last_persona", "chill")),
                last_updated_iso=str(raw.get("last_updated_iso", "")),
                day_seed=int(raw.get("day_seed", 0)),
                day_seed_date=str(raw.get("day_seed_date", "")),
            )
        except (TypeError, ValueError):
            log.warning("MoodState parse failed, using defaults: %r", raw)
            return cls()

    def to_dict(self) -> dict:
        return asdict(self)


# ════════════════════════════════════════════════════════════════
# Time-of-day & calendar baseline
# ════════════════════════════════════════════════════════════════
def _hour_msk() -> int:
    """MSK hour (UTC+3). Используется для time-of-day affordances."""
    return (datetime.now(timezone.utc) + timedelta(hours=3)).hour


def _is_party_time() -> bool:
    """Поздний вечер пт/сб (21:00-02:00) — единственное окно для HYPE.
    Раньше было 18:00, но HYPE срабатывал слишком часто и истерил.
    """
    msk_now = datetime.now(timezone.utc) + timedelta(hours=3)
    weekday = msk_now.weekday()    # 0=пн, 4=пт, 5=сб
    hour = msk_now.hour
    if weekday == 4 and hour >= 21:                   # пт поздний вечер
        return True
    if weekday == 5 and (hour >= 21 or hour < 3):     # сб поздний вечер
        return True
    if weekday == 6 and hour < 3:                     # вс ночь (= сб поздний)
        return True
    return False


def _energy_baseline() -> int:
    """Куда дрейфит energy в зависимости от времени суток (MSK)."""
    h = _hour_msk()
    # утро (6-11): низкая, нарастающая
    if 6 <= h <= 11:
        return 30 + (h - 6) * 5    # 30..55
    # день (12-17): средняя
    if 12 <= h <= 17:
        return 60
    # вечер (18-23): пик
    if 18 <= h <= 23:
        return 80
    # ночь (0-5): низкая
    return 25


def _today_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")


# ════════════════════════════════════════════════════════════════
# Signal extraction — что говорит входящее сообщение?
# ════════════════════════════════════════════════════════════════
HOSTILE_RE = re.compile(
    r"\b(тупой|дура[ck]|долбоёб|хуй|ебучий|пиздабол|заткнись|"
    r"идиот|дебил|мраз[ьb]|урод|кретин|пёс|сука\s*ты|"
    r"бот\s+тупой|тупой\s+бот|тупорылый|тупорез|"
    r"ненавижу\s+тебя|пошёл\s+нахер|катись)\b",
    re.IGNORECASE,
)

FRIENDLY_RE = re.compile(
    r"\b(спасибо|благодар|красав[аы]|ты\s+(?:норм|клас+|кру[тт]|молодец)|"
    r"люблю\s+тебя|обожаю|братан\s+ты|кайро\s+ты)\b",
    re.IGNORECASE,
)

PING_RE = re.compile(r"кайро|нагибатор|@[A-Za-z0-9_]+", re.IGNORECASE)


def _detect_signals(text: str, addressed_to_bot: bool) -> dict:
    """Вытаскивает сигналы из сообщения — что апдейтить."""
    if not text:
        return {}
    t_lower = text.lower()
    return {
        "hostile":   bool(HOSTILE_RE.search(text)) and addressed_to_bot,
        "friendly":  bool(FRIENDLY_RE.search(text)) and addressed_to_bot,
        "ping":      bool(PING_RE.search(text)),
        "is_question": "?" in text,
        "long":      len(text) > 80,
        "yelling":   text.upper() == text and len(text) > 5,
    }


# ════════════════════════════════════════════════════════════════
# State update — apply signals + decay
# ════════════════════════════════════════════════════════════════
def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def update_state(state: MoodState, text: str = "", addressed_to_bot: bool = False) -> MoodState:
    """Обновляет состояние под влиянием нового сообщения + time decay.

    Идемпотентно — можно вызывать на каждое сообщение в чате.
    """
    now = datetime.now(timezone.utc)

    # ── time-decay: тянем offended/mood/energy к baseline ──
    if state.last_updated_iso:
        try:
            prev = datetime.fromisoformat(state.last_updated_iso)
            elapsed_min = max(0, (now - prev).total_seconds() / 60.0)
        except ValueError:
            elapsed_min = 0
    else:
        elapsed_min = 0

    # offended: -1 каждые 12 минут (полный decay за ~14 часов)
    if state.offended > 0:
        decay = int(elapsed_min / 12)
        state.offended = _clamp(state.offended - decay, 0, 100)

    # mood: дрейф к 0 на 1 единицу за час
    if state.mood != 0:
        drift = int(elapsed_min / 60)
        if state.mood > 0:
            state.mood = _clamp(state.mood - drift, -100, 100)
        else:
            state.mood = _clamp(state.mood + drift, -100, 100)

    # energy: тянет к baseline текущего часа со скоростью 1 ед/12мин
    target = _energy_baseline()
    if elapsed_min > 0:
        steps = int(elapsed_min / 12)
        if state.energy < target:
            state.energy = _clamp(state.energy + steps, 0, 100)
        elif state.energy > target:
            state.energy = _clamp(state.energy - steps, 0, 100)

    # toxicity: дрейф к 30 (baseline) на 2 ед/час
    target_tox = 30
    if elapsed_min > 0:
        steps = int(elapsed_min / 30)
        if state.toxicity < target_tox:
            state.toxicity = _clamp(state.toxicity + steps, 0, 100)
        elif state.toxicity > target_tox:
            state.toxicity = _clamp(state.toxicity - steps, 0, 100)

    # ── day_seed: рандом раз в сутки сдвигает baseline ──
    today = _today_str()
    if state.day_seed_date != today:
        state.day_seed = random.randint(-30, 30)
        # Понедельник обычно хуже: дополнительно -10
        weekday = (now + timedelta(hours=3)).weekday()
        if weekday == 0:
            state.day_seed -= 10
        elif weekday == 4:    # пятница: +20 к настроению
            state.day_seed += 20
        state.day_seed_date = today
        state.mood = _clamp(state.day_seed, -100, 100)

    # ── apply incoming signals ──
    sig = _detect_signals(text, addressed_to_bot=addressed_to_bot)
    if sig.get("hostile"):
        state.offended = _clamp(state.offended + 25, 0, 100)
        state.toxicity = _clamp(state.toxicity + 10, 0, 100)
        state.mood = _clamp(state.mood - 5, -100, 100)
    if sig.get("friendly"):
        state.mood = _clamp(state.mood + 8, -100, 100)
        state.offended = _clamp(state.offended - 15, 0, 100)
    if sig.get("ping"):
        state.energy = _clamp(state.energy + 3, 0, 100)
    if sig.get("yelling"):
        state.energy = _clamp(state.energy + 2, 0, 100)

    state.last_updated_iso = now.isoformat()
    return state


# ════════════════════════════════════════════════════════════════
# Persona selector — какой модус активен прямо сейчас
# ════════════════════════════════════════════════════════════════
def select_persona(state: MoodState, last_text: str = "") -> personas.Persona:
    """Выбирает активный модус по правилам. Идемпотентно."""
    # 1. ОБИДЕЛСЯ — самый высокий приоритет
    if state.offended >= 70:
        return personas.OBIZHEN

    # 2. ОТМОРОЗ — токсичность высокая
    if state.toxicity >= 70:
        return personas.OTMOROZ

    # 4. ФИЛОСОФ — только если триггер-слово в сообщении (без рандома —
    # рандом давал 5% всегда, бот включал философа невпопад)
    if personas.has_philosophy_trigger(last_text):
        return personas.FILOSOF

    # 5. Дефолт
    return personas.CHILL


def should_stay_silent(persona: personas.Persona) -> bool:
    """Решает молчать ли вообще на это сообщение (для модуса ОБИДЕЛСЯ)."""
    return random.random() < persona.silence_chance


# ════════════════════════════════════════════════════════════════
# Debug helpers
# ════════════════════════════════════════════════════════════════
def describe(state: MoodState) -> str:
    """Краткое описание состояния для логов и дебага."""
    return (
        f"mood={state.mood:+d} energy={state.energy} "
        f"offended={state.offended} tox={state.toxicity} "
        f"persona={state.last_persona} day_seed={state.day_seed:+d}"
    )
