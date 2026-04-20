from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

CS2_APPID = 730
_VANITY = re.compile(r"^(?:https?://)?steamcommunity\.com/id/([^/]+)/?$")
_PROFILES = re.compile(r"^(?:https?://)?steamcommunity\.com/profiles/(\d{17})/?$")


async def resolve_steamid64(arg: str) -> int | None:
    """Accept: raw steamid64, vanity URL, profiles URL, or bare vanity name."""
    s = get_settings()
    arg = arg.strip()
    if arg.isdigit() and len(arg) == 17:
        return int(arg)
    m = _PROFILES.match(arg)
    if m:
        return int(m.group(1))
    m = _VANITY.match(arg)
    vanity = m.group(1) if m else arg
    if not s.steam_api_key:
        return None
    url = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
    params = {"key": s.steam_api_key, "vanityurl": vanity}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        data = r.json().get("response") or {}
    if data.get("success") == 1 and data.get("steamid"):
        return int(data["steamid"])
    return None


@dataclass
class CS2Stats:
    kills: int = 0
    deaths: int = 0
    mvps: int = 0
    hours: float = 0.0
    wins: int = 0
    rounds: int = 0

    @property
    def kd(self) -> float:
        return (self.kills / self.deaths) if self.deaths else float(self.kills)


async def fetch_cs2_stats(steamid64: int) -> CS2Stats | None:
    s = get_settings()
    if not s.steam_api_key:
        return None
    url = "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v2/"
    params = {"appid": CS2_APPID, "steamid": steamid64, "key": s.steam_api_key}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, params=params)
    if r.status_code == 403 or r.status_code == 500:
        # private profile or CS2 has no v2 for this user
        return None
    if r.status_code >= 400:
        log.warning("steam stats http %s", r.status_code)
        return None
    js = r.json().get("playerstats") or {}
    stats = {s["name"]: s["value"] for s in js.get("stats", [])}
    out = CS2Stats(
        kills=int(stats.get("total_kills", 0)),
        deaths=int(stats.get("total_deaths", 0)),
        mvps=int(stats.get("total_mvps", 0)),
        wins=int(stats.get("total_wins", 0)),
        rounds=int(stats.get("total_rounds_played", 0)),
        hours=round(int(stats.get("total_time_played", 0)) / 3600, 1),
    )
    return out


async def fetch_player_summary(steamid64: int) -> dict | None:
    s = get_settings()
    if not s.steam_api_key:
        return None
    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
    params = {"key": s.steam_api_key, "steamids": steamid64}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
    players = (r.json().get("response") or {}).get("players") or []
    return players[0] if players else None


async def fetch_cs2_inventory_summary(steamid64: int) -> dict | None:
    """Uses Steam Community inventory JSON (no auth needed for public inventories).
    Returns {count, preview_names} — pricing needs 3rd-party market API; we skip
    exact pricing to avoid paid services, just surface item count + first few.
    """
    url = f"https://steamcommunity.com/inventory/{steamid64}/{CS2_APPID}/2"
    params = {"l": "english", "count": 100}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url, params=params)
    if r.status_code != 200:
        return None
    js = r.json() or {}
    if not js.get("success"):
        return None
    descs = {d["classid"]: d for d in js.get("descriptions", [])}
    assets = js.get("assets", [])
    names = []
    for a in assets[:10]:
        d = descs.get(a["classid"])
        if d:
            names.append(d.get("market_hash_name") or d.get("name") or "—")
    return {"count": int(js.get("total_inventory_count", len(assets))), "preview": names}
