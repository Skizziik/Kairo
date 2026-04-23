"""CS2-style price calculation: base × wear × float × stattrak."""
from __future__ import annotations

import random

WEAR_BRACKETS = [
    ("factory_new", 0.00, 0.07, 1.50),
    ("minimal_wear", 0.07, 0.15, 1.20),
    ("field_tested", 0.15, 0.38, 1.00),
    ("well_worn", 0.38, 0.45, 0.70),
    ("battle_scarred", 0.45, 1.00, 0.50),
]
STATTRAK_MULTIPLIER = 1.40


def wear_from_float(f: float) -> tuple[str, float]:
    """Return (wear_name, wear_multiplier) for given float value."""
    for name, lo, hi, mult in WEAR_BRACKETS:
        if lo <= f <= hi:
            return name, mult
    return "field_tested", 1.0


def wear_label(wear: str) -> str:
    return {
        "factory_new": "Factory New",
        "minimal_wear": "Minimal Wear",
        "field_tested": "Field-Tested",
        "well_worn": "Well-Worn",
        "battle_scarred": "Battle-Scarred",
    }.get(wear, wear)


def wear_short(wear: str) -> str:
    return {
        "factory_new": "FN",
        "minimal_wear": "MW",
        "field_tested": "FT",
        "well_worn": "WW",
        "battle_scarred": "BS",
    }.get(wear, wear)


def compute_price(base_price: int, float_value: float, wear: str, stat_trak: bool) -> int:
    """Full CS-style formula: base × wear × (1 + (1-float)*0.3) × stattrak."""
    _, wear_mult = wear_from_float(float_value) if not wear else (wear, next((m for n, _, _, m in WEAR_BRACKETS if n == wear), 1.0))
    float_bonus = 1.0 + (1.0 - float_value) * 0.30
    st_mult = STATTRAK_MULTIPLIER if stat_trak else 1.0
    price = base_price * wear_mult * float_bonus * st_mult
    return max(1, int(round(price)))


def roll_float(min_float: float = 0.0, max_float: float = 1.0) -> float:
    """Biased towards middle (field-tested) to feel realistic."""
    # Triangular distribution centered near 0.20 (FT zone)
    f = random.triangular(min_float, max_float, (min_float + max_float) / 2 * 0.8 + 0.10)
    return max(min_float, min(max_float, round(f, 4)))


def rarity_label(rarity: str) -> str:
    return {
        "consumer": "Consumer Grade",
        "industrial": "Industrial Grade",
        "mil-spec": "Mil-Spec Grade",
        "restricted": "Restricted",
        "classified": "Classified",
        "covert": "Covert",
        "exceedingly_rare": "Exceedingly Rare",
    }.get(rarity, rarity)


def rarity_emoji(rarity: str) -> str:
    return {
        "consumer": "⬜",
        "industrial": "🟦",
        "mil-spec": "🟪",
        "restricted": "🟣",
        "classified": "🌸",
        "covert": "🟥",
        "exceedingly_rare": "🟨",
    }.get(rarity, "⬜")
