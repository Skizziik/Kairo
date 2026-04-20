from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class DownloadedTrack:
    path: str
    title: str
    duration: int
    uploader: str


def _ydl_opts(out_dir: str) -> dict:
    return {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 45 * 1024 * 1024,  # 45MB — TG bot upload cap for normal bots is 50MB
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }


def _download_blocking(url: str) -> DownloadedTrack | None:
    from yt_dlp import YoutubeDL

    tmp = tempfile.mkdtemp(prefix="kairo_yt_")
    opts = _ydl_opts(tmp)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info is None:
        return None
    # postprocessor converts to mp3; path becomes base + .mp3
    title = info.get("title") or "track"
    duration = int(info.get("duration") or 0)
    uploader = info.get("uploader") or ""
    # find the mp3
    for name in os.listdir(tmp):
        if name.lower().endswith(".mp3"):
            return DownloadedTrack(
                path=os.path.join(tmp, name),
                title=title,
                duration=duration,
                uploader=uploader,
            )
    return None


async def download_audio(url: str) -> DownloadedTrack | None:
    try:
        return await asyncio.to_thread(_download_blocking, url)
    except Exception:
        log.exception("yt-dlp failed")
        return None
