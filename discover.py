"""Trouve les vidéos du moment sur les chaînes surveillées (YouTube + Twitch).

Classement : vues par heure depuis la publication, soit la vitesse à laquelle
la vidéo prend des vues en ce moment.
"""
import re
import time
import urllib.parse

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
    try:  # une chaîne introuvable (handle faux, supprimée) ne doit pas bloquer les autres
        entries = _entries(f"https://www.youtube.com/{handle}/videos", limit)
    except Exception as err:
        print(f"  ! chaîne {handle} introuvable, ignorée : {str(err)[:120]}")
        return out
    for e in entries:
        url = e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
        try:  # le listing "flat" n'a pas la date : on récupère le détail
            c = _candidate(_details(url), "youtube", handle)
        except Exception as err:
            print(f"  ! {url}: {err}")
            continue
        if c:
            out.append(c)
    return out


# Recherche YouTube triée par date de mise en ligne, vidéos de cette semaine seulement
SEARCH_SP = "CAISBAgCEAE%3D"
FR_WORDS = {"le", "la", "les", "des", "du", "une", "un", "et", "est", "pour", "sur", "avec",
            "qui", "dans", "pas", "tout", "mon", "ma", "mes", "je", "on", "ce", "cette", "enfin",
            "nouveau", "nouvelle", "découverte", "jamais", "voici", "c'est", "j'ai", "il", "elle",
            "sortie", "jeu", "carte", "secret", "secrets", "incroyable", "première", "premier"}
EN_WORDS = {"the", "and", "you", "your", "this", "that", "with", "for", "new", "everything",
            "things", "need", "know", "how", "why", "what", "is", "are", "of", "in", "on", "my"}


def title_lang(title):
    """Langue probable d'un titre (fr / en / ?) : accents et petits mots courants."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", title.lower())
    fr = sum(w in FR_WORDS for w in words) + 2 * bool(re.search(r"[àâçéèêëîïôûùœ]", title.lower()))
    en = sum(w in EN_WORDS for w in words)
    return "fr" if fr > en else "en" if en > fr else "?"


def youtube_search(query, limit=15, title_ok=None, lang=None):
    """Vidéos de la semaine pour une recherche YouTube, les plus récentes d'abord."""
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp={SEARCH_SP}"
    out = []
    for e in _entries(url, limit):
        if title_ok and not title_ok(e.get("title") or ""):
            continue
        try:  # le listing de recherche n'a pas la date : on récupère le détail
            info = _details(f"https://www.youtube.com/watch?v={e['id']}")
            # langue déclarée par YouTube (audio) : une vidéo anglaise est écartée d'une chaîne fr
            spoken = (info.get("language") or "")[:2]
            if lang and spoken and spoken != lang:
                continue
            c = _candidate(info, "youtube", e.get("channel") or query)
        except Exception as err:
            print(f"  ! {e.get('id')}: {str(err)[:100]}")
            continue
        if c:
            out.append(c)
    return out


TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"   # client web public de Twitch (celui de yt-dlp)


def twitch_meta(urls):
    """Catégorie (jeu) et nom du streamer de chaque clip / VOD Twitch : {url: (catégorie, streamer)}.
    yt-dlp ne donne ni l'un ni l'autre (son « uploader » est la personne qui a créé le clip)."""
    import json
    import urllib.request
    qs, keys = [], []
    for u in urls:
        m = re.search(r"/clip/([\w-]+)", u) or re.search(r"clips\.twitch\.tv/([\w-]+)", u)
        v = re.search(r"/videos/(\d+)", u)
        if m:
            qs.append({"query": "query($s:ID!){clip(slug:$s){game{name} broadcaster{displayName}}}",
                       "variables": {"s": m.group(1)}})
        elif v:
            qs.append({"query": "query($s:ID!){video(id:$s){game{name} owner{displayName}}}",
                       "variables": {"s": v.group(1)}})
        else:
            continue
        keys.append(u)
    out = {}
    for i in range(0, len(qs), 30):
        try:
            req = urllib.request.Request(TWITCH_GQL, data=json.dumps(qs[i:i + 30]).encode(),
                                         headers={"Client-ID": TWITCH_CLIENT,
                                                  "Content-Type": "application/json"})
            res = json.load(urllib.request.urlopen(req, timeout=20))
        except Exception as err:
            print(f"  ! catégories Twitch indisponibles : {str(err)[:80]}")
            continue
        for u, r in zip(keys[i:i + 30], res):
            d = r.get("data") or {}
            node = d.get("clip") or d.get("video") or {}
            who = node.get("broadcaster") or node.get("owner") or {}
            out[u] = ((node.get("game") or {}).get("name") or "", who.get("displayName") or "")
    return out


def gaming_ok(c, w):
    """Exception à la règle « pas de gaming » : Squeezie (sa chaîne, son live, ou un live où il
    est avec d'autres, repéré par son nom dans le titre). Réglage watch.gaming_ok (regex)."""
    rx = w.get("gaming_ok")
    if not rx:
        return False
    text = " ".join(str(c.get(k) or "") for k in ("source", "creator", "title"))
    return bool(re.search(rx, text, re.I))


def twitch_channel(name, limit=8, categories=None, vods=True, w=None):
    """Clips (et VODs) récents d'un streamer. `categories` = catégories Twitch acceptées
    (ex. Just Chatting) : tout le reste (jeux vidéo) est écarté, ainsi que ce dont la
    catégorie est inconnue."""
    out = []
    feeds = [f"https://www.twitch.tv/{name}/clips?filter=clips&range=7d"]  # clips les plus vus
    if vods:
        feeds.append(f"https://www.twitch.tv/{name}/videos?filter=archives&sort=time")  # VODs
    for feed in feeds:
        try:
            for e in _entries(feed, limit):
                c = _candidate(e, "twitch", name)
                if c and c["url"]:
                    out.append(c)
        except Exception as err:
            print(f"  ! twitch {feed}: {err}")
    meta = twitch_meta([c["url"] for c in out])
    keep, dropped = [], []
    for c in out:
        game, who = meta.get(c["url"], ("", ""))
        c["category"] = game
        if who:
            c["creator"] = who   # le streamer, pas la personne qui a créé le clip
        if categories is not None and game.lower() not in categories and not gaming_ok(c, w or {}):
            dropped.append(game or "?")
            continue
        keep.append(c)
    if dropped:
        print(f"  {len(dropped)} écarté(s) hors niche : " + ", ".join(sorted(set(dropped)))[:120])
    return keep


def _recent_vph(c, seen, now):
    """Vues/heure depuis le dernier relevé (passage précédent) : ce que la vidéo prend EN CE
    MOMENT. None si on ne l'a jamais vue ou si le relevé est trop récent (< 3 h)."""
    prev = seen.get(c["id"])
    if not prev:
        return None
    hours = (now - prev[0]) / 3600
    if hours < 3:
        prev = prev[2:] or None   # relevé d'avant (gardé pour les passages rapprochés)
        if not prev:
            return None
        hours = (now - prev[0]) / 3600
        if hours < 3:
            return None
    return max(c["views"] - prev[1], 0) / hours


def find_trending(cfg, already_done, state=None, want=None):
    """Stratégie fraîcheur :
    1) d'abord les vidéos de la veille et du jour (moins de `fresh_hours`), classées par vues/heure ;
    2) puis, en complément, des vidéos plus vieilles (jusqu'à `max_age_days`) SEULEMENT si elles
       prennent encore des vues maintenant (vues/heure mesurées entre deux passages).
    `want` = nombre de vidéos sources utiles aujourd'hui (calculé d'après la file d'attente)."""
    w = cfg.get("watch") or {}
    # chaîne thématique (ex. GTA 6) : seulement les vidéos dont le titre parle du sujet,
    # et dans la langue de la chaîne
    topic = re.compile(w["title_match"], re.I) if w.get("title_match") else None
    lang = w.get("title_lang")

    def title_ok(t):
        if topic and not topic.search(t or ""):
            return False
        return not lang or title_lang(t or "") in (lang, "?")
    cands = []
    seen_ids = set()
    for q in w.get("search") or []:
        print(f"[découverte] recherche YouTube « {q} »")
        for c in youtube_search(q, w.get("search_limit", 12), title_ok, lang):
            if c["id"] not in seen_ids and c["id"] not in already_done:
                seen_ids.add(c["id"])
                cands.append(c)
    for h in w.get("youtube") or []:
        if "REMPLACE_MOI" in h:
            continue
        print(f"[découverte] YouTube {h}")
        cands += youtube_channel(h)
    cats = w.get("twitch_categories")
    cats = {x.lower() for x in cats} if cats else None
    for n in w.get("twitch") or []:
        if "REMPLACE_MOI" in n:
            continue
        print(f"[découverte] Twitch {n}")
        cands += twitch_channel(n, categories=cats, vods=w.get("twitch_vods", True), w=w)

    if topic or lang:   # chaînes et streamers généralistes : on garde ce qui parle du sujet
        cands = [c for c in cands if title_ok(c["title"])]
    if w.get("exclude_title"):   # hors niche (gaming, sport...) d'après le titre
        bad_t = re.compile(w["exclude_title"], re.I)
        drop = [c for c in cands if bad_t.search(c.get("title") or "") and not gaming_ok(c, w)]
        if drop:
            print(f"  {len(drop)} vidéo(s) hors niche (titre) : "
                  + " | ".join(c["title"][:40] for c in drop)[:200])
        cands = [c for c in cands if c not in drop]
    if w.get("exclude_always"):   # sport : interdit même pour Squeezie
        bad_a = re.compile(w["exclude_always"], re.I)
        cands = [c for c in cands if not bad_a.search(c.get("title") or "")]
    if w.get("exclude_sources"):   # chaînes TV / médias : Content ID bloque leurs images
        bad = re.compile(w["exclude_sources"], re.I)
        drop = [c for c in cands if bad.search(c.get("source") or "")]
        if drop:
            print(f"  {len(drop)} vidéo(s) de chaînes exclues (TV/médias) : "
                  + ", ".join(sorted({c['source'] for c in drop}))[:150])
        cands = [c for c in cands if not bad.search(c.get("source") or "")]
    now = time.time()
    seen = (state or {}).setdefault("views_seen", {}) if state is not None else {}
    fresh_h = w.get("fresh_hours", 36)
    max_h = w.get("max_age_days", 5) * 24
    min_v = w.get("min_views", 0)
    min_len = cfg.get("min_seconds", 25)
    ok = [c for c in cands
          if c["id"] not in already_done
          and c["age_hours"] <= max_h
          and (c["duration"] or 999) >= min(min_len, 15)
          and (c["duration"] or 0) <= w.get("max_source_minutes", 120) * 60]

    tw_min = w.get("twitch_min_views", 300)
    fresh, old = [], []
    for c in ok:
        floor = tw_min if c["platform"] == "twitch" else min_v
        if c["age_hours"] <= fresh_h:
            # vidéo du jour ou de la veille : le seuil de vues est réduit au prorata de son âge
            # (une vidéo de 6 h n'a pas encore eu le temps de faire 100 000 vues)
            need = floor * min(c["age_hours"] / 24, 1) * w.get("fresh_views_factor", 0.5)
            if c["views"] >= need:
                c["why_pick"] = "récente"
                fresh.append(c)
        else:
            rv = _recent_vph(c, seen, now)
            c["recent_vph"] = None if rv is None else round(rv)
            # plus vieille : elle doit avoir percé ET prendre encore des vues aujourd'hui
            hot = w.get("old_min_vph", max(floor / 48, 50))
            still = rv if rv is not None else c["views_per_hour"] * w.get("old_unknown_factor", 0.5)
            if c["views"] >= floor and still >= hot:
                c["why_pick"] = f"ancienne, encore {round(still)} vues/h"
                c["rank"] = still
                old.append(c)

    # relevé des vues pour mesurer la vitesse actuelle au prochain passage
    for c in cands:
        prev = seen.get(c["id"]) or []
        if not prev or now - prev[0] >= 3 * 3600:
            seen[c["id"]] = [now, c["views"]] + prev[:2]
    for k in [k for k, v in seen.items() if now - v[0] > 15 * 86400]:
        seen.pop(k)

    fresh.sort(key=lambda c: c["views_per_hour"], reverse=True)
    old.sort(key=lambda c: c["rank"], reverse=True)
    n = w.get("videos_per_run", 2) if want is None else want
    old_quota = min(w.get("old_per_run", 1), n)
    picks = fresh[: n - old_quota] + old[:old_quota]
    if len(picks) < n:   # pas assez de récentes : on complète avec ce qui marche encore
        rest = [c for c in fresh + old if c not in picks]
        picks += rest[: n - len(picks)]
    print(f"  {len(fresh)} récente(s) (< {fresh_h} h), {len(old)} plus vieille(s) qui marchent encore")
    for c in picks:
        print(f"  {c['views_per_hour']:>8} vues/h  {c['age_hours']:>5.0f} h  {c['platform']:<7} "
              f"{c['why_pick']:<28} {c['title'][:60]}")
    for c in picks:
        c["source_ts"] = now - c["age_hours"] * 3600
    return picks
