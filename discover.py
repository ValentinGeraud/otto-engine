"""Trouve les vidéos du moment sur les chaînes surveillées (YouTube + Twitch).

Classement : vues par heure depuis la publication, soit la vitesse à laquelle
la vidéo prend des vues en ce moment.
"""
import time

import yt_dlp

_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True}


def _entries(url, limit):
    opts = dict(_OPTS, extract_flat="in_playlist", playlistend=limit)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e for e in (info.get("entries") or []) if e]


def _details(url):
    with yt_dlp.YoutubeDL(_OPTS) as ydl:
        return ydl.extract_info(url, download=False)


def _candidate(e, platform, source):
    ts = e.get("timestamp") or e.get("release_timestamp")
    if not ts and e.get("upload_date"):
        ts = time.mktime(time.strptime(e["upload_date"], "%Y%m%d"))
    views = e.get("view_count") or 0
    if not ts:
        return None
    age_h = max((time.time() - ts) / 3600, 1)
    return {
        "url": e.get("webpage_url") or e.get("url"),
        "id": f"{platform}_{e.get('id')}",
        "title": e.get("title", ""),
        "platform": platform,
        "source": source,
        "views": views,
        "age_hours": round(age_h, 1),
        "views_per_hour": round(views / age_h),
        "duration": e.get("duration") or 0,
    }


def youtube_channel(handle, limit=8):
    handle = handle if handle.startswith("@") else "@" + handle
    out = []
    for e in _entries(f"https://www.youtube.com/{handle}/videos", limit):
        url = e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
        try:  # le listing "flat" n'a pas la date : on récupère le détail
            c = _candidate(_details(url), "youtube", handle)
        except Exception as err:
            print(f"  ! {url}: {err}")
            continue
        if c:
            out.append(c)
    return out


def twitch_channel(name, limit=8):
    out = []
    feeds = [
        f"https://www.twitch.tv/{name}/clips?filter=clips&range=7d",  # clips les plus vus
        f"https://www.twitch.tv/{name}/videos?filter=archives&sort=time",  # derniers VODs
    ]
    for feed in feeds:
        try:
            for e in _entries(feed, limit):
                c = _candidate(e, "twitch", name)
                if c and c["url"]:
                    out.append(c)
        except Exception as err:
            print(f"  ! twitch {feed}: {err}")
    return out


def find_trending(cfg, already_done):
    w = cfg.get("watch") or {}
    cands = []
    for h in w.get("youtube") or []:
        if "REMPLACE_MOI" in h:
            continue
        print(f"[découverte] YouTube {h}")
        cands += youtube_channel(h)
    for n in w.get("twitch") or []:
        if "REMPLACE_MOI" in n:
            continue
        print(f"[découverte] Twitch {n}")
        cands += twitch_channel(n)

    max_h = w.get("max_age_days", 7) * 24
    min_v = w.get("min_views", 0)
    min_len = cfg.get("min_seconds", 25)
    keep = [c for c in cands
            if c["id"] not in already_done
            and c["age_hours"] <= max_h
            and c["views"] >= (w.get("twitch_min_views", 300) if c["platform"] == "twitch" else min_v)
            and (c["duration"] or 999) >= min(min_len, 15)
            and (c["duration"] or 0) <= w.get("max_source_minutes", 120) * 60]
    keep.sort(key=lambda c: c["views_per_hour"], reverse=True)
    for c in keep[:10]:
        print(f"  {c['views_per_hour']:>8} vues/h  {c['platform']:<7} {c['title'][:70]}")
    return keep[: w.get("videos_per_run", 2)]
