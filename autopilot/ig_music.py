"""IG music settings (貼文 / 限動), stored in the event YAML under ig.music.post / ig.music.story.

Each entry: {song, title, artist, url, start: "mm:ss", end: "mm:ss"}. An empty song means "no music"
(the IG flow then only removes IG's auto-attached song and adds nothing)."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Callable

KINDS = {"post": "貼文", "story": "限動"}
MIN_S, MAX_S = 5, 90  # IG post clip length range
LIMITS = {"post": (5, 90), "story": (5, 45)}  # story: dashboard allows up to 45 s; the phone's real max is read
# during the run and a longer request is cut to it (with a warning)
_MMSS = re.compile(r"^\s*(\d{1,2}):([0-5]\d)\s*$")
_YT = re.compile(r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)


class MusicError(ValueError):
    pass


def parse_mmss(s: str) -> int:
    m = _MMSS.match(str(s or ""))
    if not m:
        raise MusicError(f"時間格式要是 mm:ss（例如 0:30），收到「{s}」")
    return int(m.group(1)) * 60 + int(m.group(2))


def fmt_mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def is_youtube(s: str) -> bool:
    return bool(_YT.match(str(s or "").strip()))


def split_title(title: str, author: str = "") -> tuple[str, str]:
    """'Artist - Song (Official MV)' -> ('Song', 'Artist'). Falls back to the channel name."""
    t = re.sub(r"\s*[\(\[【（][^)\]】）]*(official|mv|m/v|video|audio|lyric|歌詞|官方|完整版|4k|hd)[^)\]】）]*[\)\]】）]",
               "", title or "", flags=re.I).strip()
    for sep in (" - ", " – ", " — ", "｜", " | ", "－"):
        if sep in t:
            a, b = t.split(sep, 1)
            if a.strip() and b.strip():
                return b.strip(), a.strip()
    artist = re.sub(r"\s*-\s*Topic$", "", author or "").strip()
    artist = re.sub(r"(VEVO|Official|官方頻道)$", "", artist, flags=re.I).strip()
    return t, artist


def _oembed(url: str) -> dict:
    q = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(q, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_song(song: str, fetch: Callable[[str], dict] | None = None) -> dict:
    """'歌名 / 歌手', '歌名 - 歌手' or a YouTube link -> {song, title, artist, url}."""
    song = (song or "").strip()
    if not song:
        return {"song": "", "title": "", "artist": "", "url": ""}
    if is_youtube(song):
        url = song if song.startswith("http") else "https://" + song
        try:
            info = (fetch or _oembed)(url)
        except Exception as e:
            raise MusicError(f"讀不到這個 YouTube 連結的標題（{type(e).__name__}），請改填「歌名／歌手」。")
        title, artist = split_title(info.get("title", ""), info.get("author_name", ""))
        if not title:
            raise MusicError("YouTube 標題是空的，請改填「歌名／歌手」。")
        return {"song": song, "title": title, "artist": artist, "url": url}
    for sep in ("／", "/", " - ", "－", "|", "｜"):
        if sep in song:
            a, b = song.split(sep, 1)
            return {"song": song, "title": a.strip(), "artist": b.strip(), "url": ""}
    return {"song": song, "title": song, "artist": "", "url": ""}


def build_entry(song: str, start: str, end: str, fetch: Callable[[str], dict] | None = None,
                kind: str = "post") -> dict:
    e = parse_song(song, fetch)
    if not e["song"]:
        return {}
    s = parse_mmss(start or "0:00")
    if not (end or "").strip():
        raise MusicError("請填結束時間（mm:ss）。")
    t = parse_mmss(end)
    dur = t - s
    lo, hi = LIMITS.get(kind, (MIN_S, MAX_S))
    if dur < lo or dur > hi:
        raise MusicError(f"片段長度 {dur} 秒；IG {KINDS.get(kind, '')}音樂只能 {lo}～{hi} 秒，請調整開始／結束時間。")
    e.update(start=fmt_mmss(s), end=fmt_mmss(t))
    return e


def get(raw: dict, kind: str) -> dict:
    m = (((raw or {}).get("ig") or {}).get("music") or {}).get(kind) or {}
    return m if (m.get("title") or m.get("song")) else {}


def to_job(entry: dict) -> dict | None:
    """Settings -> what the phone flow needs: search query, title/artist to match, start & duration (s)."""
    if not entry:
        return None
    s, t = parse_mmss(entry.get("start") or "0:00"), parse_mmss(entry["end"])
    title, artist = entry.get("title") or entry.get("song"), entry.get("artist") or ""
    return {"title": title, "artist": artist, "query": f"{title} {artist}".strip(),
            "start_s": s, "dur_s": t - s, "label": f"{title}" + (f" — {artist}" if artist else "")}


def view(raw: dict) -> list[dict]:
    out = []
    for k, name in KINDS.items():
        e = get(raw, k)
        lo, hi = LIMITS[k]
        out.append({"kind": k, "name": name, "min_s": lo, "max_s": hi, "song": e.get("song", ""), "title": e.get("title", ""),
                    "artist": e.get("artist", ""), "start": e.get("start", ""), "end": e.get("end", ""),
                    "dur": (parse_mmss(e["end"]) - parse_mmss(e.get("start") or "0:00")) if e.get("end") else None})
    return out
