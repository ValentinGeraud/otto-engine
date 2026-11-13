"""Publication 100 % gratuite.

- manual      : prépare a_poster/<date heure>/ avec la vidéo et les textes prêts à coller.
                On programme ensuite tout d'un coup dans TikTok Studio, Meta Business Suite
                et YouTube Studio (gratuits).
- youtube_api : envoie en plus directement sur YouTube (API officielle gratuite) avec
                publication programmée.
"""
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
QUEUE = ROOT / "a_poster"
SECRETS = ROOT / "secrets"
TOKEN = SECRETS / "youtube_token.json"
CLIENT_SECRET = SECRETS / "client_secret.json"
PROJECT_QUOTA = ROOT / "quota.json"   # envois du jour par projet Google Cloud (partagé entre chaînes)
LANG = "fr"
CATEGORY = "22"
CHANNEL_ID = ""   # si renseigné (publish.youtube_channel_id), la vérification se fait sur l'ID
LABELS = {"fr": ("À retenir", "Extrait de"),
          "en": ("Key takeaway", "Source"),
          "es": ("La clave", "Fuente")}
# Hashtags ajoutés à chaque Short si la config de la chaîne n'en donne pas (publish.hashtags)
DEFAULT_HASHTAGS = {"fr": ["clip", "youtubeur", "drole"],
                    "en": ["business", "entrepreneur", "money"],
                    "es": ["historiasreales", "historias", "misterio"]}
HASHTAGS = DEFAULT_HASHTAGS["fr"]
CHANNEL_SLUG = "main"
CAPTION_PREFIX = ""       # publish.caption_prefix : 1re ligne imposée (tag d'une campagne)
REQUIRED_HASHTAGS = []    # publish.required_hashtags : hashtags imposés, toujours en tête
MAX_HASHTAGS = 8  # YouTube affiche les 3 premiers au-dessus du titre ; au-delà de 60 il les ignore tous


def configure(cfg):
    """Bascule sur une chaîne (voir channels.py) : dossiers, jeton YouTube, langue."""
    global QUEUE, TOKEN, CLIENT_SECRET, LANG, CATEGORY, CHANNEL_ID, HASHTAGS, _yt
    global CAPTION_PREFIX, REQUIRED_HASHTAGS, CHANNEL_SLUG
    QUEUE, TOKEN, CLIENT_SECRET = cfg["_queue"], cfg["_token"], cfg["_client_secret"]
    CHANNEL_SLUG = cfg.get("_id") or "main"
    CAPTION_PREFIX = (cfg.get("publish") or {}).get("caption_prefix", "")
    REQUIRED_HASHTAGS = (cfg.get("publish") or {}).get("required_hashtags") or []
    CHANNEL_ID = (cfg.get("publish") or {}).get("youtube_channel_id", "")
    LANG = cfg.get("language", "fr")
    CATEGORY = str((cfg.get("publish") or {}).get("category_id", 22))
    HASHTAGS = (cfg.get("publish") or {}).get("hashtags") or DEFAULT_HASHTAGS.get(LANG, [])
    _yt = None
    import buffer   # compte Buffer de la chaîne (plusieurs comptes = plusieurs clés API)
    buffer.KEY_NAME = (cfg.get("publish") or {}).get("buffer_key", "api_key")


def _tag(word):
    """« Zack Nani » → ZackNani (un hashtag ne contient ni espace ni ponctuation)."""
    return re.sub(r"[^\w]", "", str(word).lstrip("#"), flags=re.UNICODE)[:30]


def split_hashtags(meta):
    """(texte de la description sans hashtags, liste de hashtags sans #, dédoublonnée).
    Ordre : #shorts, ceux de l'IA (les plus précis), le créateur, puis ceux de la chaîne."""
    caption = meta.get("caption") or meta.get("title") or ""
    found = re.findall(r"#(\w+)", caption, flags=re.UNICODE)
    text = re.sub(r"\s*#\w+", "", caption, flags=re.UNICODE).strip()
    tags, seen = [], set()
    # hashtags exigés par une campagne en tête : ils ne sont jamais coupés par MAX_HASHTAGS
    for t in [*REQUIRED_HASHTAGS, "shorts", *found, meta.get("credit", ""), *HASHTAGS]:
        t = _tag(t)
        if t and not t.isdigit() and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    return text, tags[:MAX_HASHTAGS]


def description(meta, extra=""):
    """Description complète : texte, ajouts éventuels, puis la ligne de hashtags."""
    text, tags = split_hashtags(meta)
    # tag exigé par une campagne en PREMIÈRE ligne (ex. « @iamlexvalentino »)
    first = f"{CAPTION_PREFIX}\n" if CAPTION_PREFIX else ""
    return f"{first}{text}{extra}\n\n" + " ".join("#" + t for t in tags)


def day_slots(pub):
    """Heures de publication d'une journée : liste fixe, ou `per_day` réparties sur la plage."""
    n = pub.get("per_day")
    if pub.get("_custom_slots"):  # horaires choisis dans le calendrier, plafonnés par le palier anti-spam
        cap = ramp_today(pub)
        if pub.get("backend") == "youtube_api":
            cap = min(cap, pub.get("api_uploads_per_day", 6))
        return sorted(pub["slots"])[:cap]
    if not n:
        return pub["slots"]
    n = min(n, ramp_today(pub))
    if pub.get("backend") == "youtube_api":  # tant que le quota est bas, on espace les posts
        n = min(n, pub.get("api_uploads_per_day", 6))
    a, b = pub.get("window", ["07:00", "23:30"])
    t0 = int(a[:2]) * 60 + int(a[3:])
    t1 = int(b[:2]) * 60 + int(b[3:])
    step = (t1 - t0) / max(n - 1, 1)
    return [f"{int(t0 + i * step) // 60:02d}:{int(t0 + i * step) % 60:02d}" for i in range(n)]


def ramp_today(pub):
    """Montée en charge : nombre max de Shorts/jour selon l'ancienneté de la chaîne."""
    r = pub.get("ramp")
    if not r:
        return 10**6
    start = datetime.strptime(str(r["start"]), "%Y-%m-%d").date()
    week = max((datetime.now().date() - start).days // 7, 0)
    steps = r["per_day_by_week"]
    return steps[min(week, len(steps) - 1)]


def next_slots(pub, taken, count):
    """Prochains créneaux libres (datetime avec fuseau)."""
    tz = ZoneInfo(pub.get("timezone", "Europe/Paris"))
    now = datetime.now(tz)
    slots, day = [], now.date()
    taken = set(taken) | set((pub.get("_pins") or {}).values())  # créneaux réservés à des clips épinglés
    off = set(pub.get("_off_days") or [])
    # écart minimum avec un post déjà programmé (ex. ancienne cadence : 12h00 et 12h06 = trop proche)
    gap = timedelta(minutes=pub.get("min_gap_minutes", 0))
    near = []
    if gap:
        for t in taken:
            try:
                near.append(datetime.fromisoformat(t))
            except (TypeError, ValueError):
                pass
    while len(slots) < count:
        if day.isoformat() in off:  # jour sans sortie (réglé depuis le calendrier)
            day += timedelta(days=1)
            continue
        for hm in day_slots(pub):
            h, m = map(int, hm.split(":"))
            dt = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if dt > now + timedelta(minutes=30) and dt.isoformat() not in taken \
                    and dt not in slots and not any(abs(dt - t) < gap for t in near):
                slots.append(dt)
                if len(slots) == count:
                    break
        day += timedelta(days=1)
    return slots


def queue_manual(clip_path: Path, meta, when: datetime):
    """Dossier prêt à poster : video.mp4 + A_COLLER.txt."""
    folder = QUEUE / f"{when:%Y-%m-%d_%Hh%M}"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(clip_path, folder / "video.mp4")
    caption = description(meta)
    (folder / "A_COLLER.txt").write_text(f"""À PUBLIER LE {when:%d/%m/%Y à %Hh%M}

===== TikTok (TikTok Studio > Téléverser > Programmer) =====
{caption}

===== Instagram Reels (Meta Business Suite > Créer un reel > Programmer) =====
{caption}

===== YouTube Shorts (YouTube Studio > Créer > Planifier) =====
Titre : {meta['title'][:100]}
Description :
{caption}

----- Pour info -----
Hook : {meta.get('hook', '')}
Score viralité : {meta.get('score')}/10. Pourquoi : {meta.get('why', '')}
Source : {meta.get('source', '')}
""", encoding="utf-8")
    print(f"    → a_poster/{folder.name}/")
    return str(folder.relative_to(ROOT))


# ---------- YouTube Data API (gratuit) ----------
_yt = None


def _youtube():
    global _yt
    if _yt:
        return _yt
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube.upload",
              "https://www.googleapis.com/auth/youtube.readonly"]
    token = TOKEN
    creds = Credentials.from_authorized_user_file(str(token), scopes) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        secret = CLIENT_SECRET
        if not secret.exists():
            raise RuntimeError(f"secrets/{secret.name} manquant (voir README, section YouTube)")
        creds = InstalledAppFlow.from_client_secrets_file(str(secret), scopes).run_local_server(
            port=8765, open_browser=False,
            authorization_prompt_message="AUTH_URL {url}",
            success_message="La chaîne est connectée à YouTube. Tu peux fermer cet onglet.")
    token.write_text(creds.to_json(), encoding="utf-8")
    _yt = build("youtube", "v3", credentials=creds)
    return _yt


def check_channel(expected):
    """Refuse d'envoyer si le compte connecté n'est pas la bonne chaîne."""
    ch = _youtube().channels().list(part="snippet", mine=True).execute()["items"][0]
    name = ch["snippet"]["title"]
    if CHANNEL_ID:  # identifiant unique (UC...) : fiable même si le nom de la chaîne change
        if ch["id"] != CHANNEL_ID:
            raise RuntimeError(f"connecté à la chaîne « {name} » ({ch['id']}) au lieu de "
                               f"{CHANNEL_ID}. Supprime secrets/{TOKEN.name} et relance "
                               "youtube_auth.py")
        return name
    if expected and name.strip().lower() != expected.strip().lower():
        raise RuntimeError(f"connecté à la chaîne « {name} » au lieu de « {expected} ». "
                           f"Supprime secrets/{TOKEN.name} et relance youtube_auth.py")
    return name


def upload_youtube(clip_path: Path, meta, when: datetime, expected_channel=""):
    from googleapiclient.http import MediaFileUpload
    check_channel(expected_channel)
    take, src = LABELS.get(LANG, LABELS["en"])
    _, tags = split_hashtags(meta)
    body = {
        "snippet": {"title": meta["title"][:100],
                    "description": description(
                        meta,
                        (f"\n\n{take} : {meta['takeaway']}" if meta.get("takeaway") else "")
                        + (f"\n\n💬 {meta['question']}" if meta.get("question") else "")
                        + (f"\n\n{src} : {meta['source']}" if meta.get("source") else "")),
                    "tags": tags,   # mots-clés cachés (recherche YouTube), sans #
                    "categoryId": CATEGORY,
                    "defaultLanguage": LANG, "defaultAudioLanguage": LANG},
        "status": {"privacyStatus": "private",  # obligatoire pour une publication programmée
                   "publishAt": when.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "selfDeclaredMadeForKids": False},
    }
    req = _youtube().videos().insert(part="snippet,status", body=body,
                                     media_body=MediaFileUpload(str(clip_path), resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print(f"    ✓ YouTube programmé ({when:%d/%m %Hh%M}) : https://youtu.be/{resp['id']}")
    return resp["id"]


def _quota_day():
    # le quota de l'API YouTube se remet à zéro à minuit, heure du Pacifique
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _project_quota():
    return json.loads(PROJECT_QUOTA.read_text(encoding="utf-8")) if PROJECT_QUOTA.exists() else {}


def _project_used():
    """Envois du jour sur le projet Google Cloud. Le quota de l'API est PAR PROJET : il est
    partagé par toutes les chaînes qui utilisent le même client_secret."""
    day = _quota_day()
    d = _project_quota().get(CLIENT_SECRET.name, {})
    if day in d:
        return d[day]
    main = ROOT / "state.json"  # avant le multi-chaînes, seul state.json comptait
    if CLIENT_SECRET.name == "client_secret.json" and main.exists():
        return json.loads(main.read_text(encoding="utf-8")).get("yt_uploads", {}).get(day, 0)
    return 0


def _project_set(n):
    q = _project_quota()
    q.setdefault(CLIENT_SECRET.name, {})[_quota_day()] = n
    q[CLIENT_SECRET.name] = dict(sorted(q[CLIENT_SECRET.name].items())[-14:])
    PROJECT_QUOTA.write_text(json.dumps(q, indent=1), encoding="utf-8")


def per_day_capacity(pub):
    """Nombre de posts qui peuvent sortir en une journée sur la cible la plus rapide
    (Buffer : buffer_per_day ; YouTube : palier anti-spam et quota)."""
    yt = 0
    if pub.get("backend") == "youtube_api":
        yt = min(ramp_today(pub), pub.get("api_uploads_per_day", 6), pub.get("per_day") or 10**6)
    buf = pub.get("buffer_per_day", 10) if pub.get("buffer_channels") else 0
    return max(yt, buf, 1)


def _clip_age_days(c):
    """Âge du contenu d'un clip : date de la vidéo source si connue, sinon date du montage."""
    import time
    ts = c.get("source_ts") or c.get("made")
    if not ts:
        f = ROOT / c["file"]
        if not f.exists():
            return None
        ts = f.stat().st_mtime
    return (time.time() - ts) / 86400


def drop_stale(pub, state, save):
    """Stratégie fraîcheur : un clip encore en attente dont le contenu a plus de
    `max_clip_age_days` jours ne part plus (place aux vidéos de la veille et du jour).
    Les campagnes à source fixe (Lex, Dane) mettent max_clip_age_days: 0 = jamais retiré."""
    limit = pub.get("max_clip_age_days", 3)
    if not limit:
        return 0
    n = 0
    for c in state["clips"]:
        p = c.get("publish") or {}
        if not (p.get("pending") or p.get("yt_pending")) or p.get("dropped"):
            continue
        age = _clip_age_days(c)
        if age is None or age <= limit:
            continue
        if p.get("pending"):          # cible principale jamais servie : le clip est retiré
            p.pop("pending", None)
            p.pop("yt_pending", None)
            p["dropped"] = f"{datetime.now():%Y-%m-%d} : trop vieux ({age:.0f} j), stratégie fraîcheur"
        else:                          # déjà sur Buffer : on renonce seulement à YouTube
            p.pop("yt_pending", None)
            p["yt_dropped"] = f"{datetime.now():%Y-%m-%d} : trop vieux ({age:.0f} j)"
        n += 1
    if n:
        print(f"  {n} clip(s) trop vieux retirés de la file (plus de {limit} j)")
        save(state)
    return n


def _freshness(c):
    return c.get("source_ts") or c.get("made") or 0


def quota_left(pub, state):
    """Envois YouTube encore possibles aujourd'hui (0 si la chaîne ne publie pas sur YouTube)."""
    if pub.get("backend") != "youtube_api":
        return 0
    used = state.setdefault("yt_uploads", {}).get(_quota_day(), 0)
    chan = min(pub.get("api_uploads_per_day", 6), ramp_today(pub)) - used
    return min(chan, pub.get("project_uploads_per_day", 6) - _project_used())


def buffer_room(pub, state):
    """Places libres dans la file Buffer (10 posts programmés max par compte, plan Free)."""
    import buffer
    chans = pub.get("buffer_channels") or []
    return min((buffer.QUEUE_MAX - buffer.queued(state, c["id"]) for c in chans), default=0)


class QuotaExceeded(Exception):
    pass


def _publish_youtube(clip_path, meta, pub, when, state, res):
    if quota_left(pub, state) <= 0:
        print("    … YouTube : quota API du jour atteint, en file d'attente")
        return
    try:
        res["youtube_id"] = upload_youtube(clip_path, meta, when, pub.get("youtube_channel", ""))
        res["youtube_when"] = when.isoformat()
        d = state["yt_uploads"]
        d[_quota_day()] = d.get(_quota_day(), 0) + 1
        _project_set(_project_used() + 1)
        res.pop("youtube_error", None)
    except Exception as e:
        if "quotaExceeded" in str(e):  # quota du projet épuisé : toutes ses chaînes attendent
            _project_set(10**6)
        if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
            state["yt_uploads"][_quota_day()] = 10**6  # bloqué jusqu'à demain
        res["youtube_error"] = str(e)[:300]
        print(f"    ✗ YouTube : {str(e)[:200]}")


def buffer_pub(pub):
    """Réglages de cadence propres à Buffer (TikTok, Instagram), indépendants de YouTube :
    publish.buffer_per_day, buffer_window, buffer_ramp (sinon ceux de YouTube)."""
    out = dict(pub)
    out["backend"] = "buffer"
    out["per_day"] = pub.get("buffer_per_day") or pub.get("per_day")
    out["ramp"] = pub.get("buffer_ramp")
    out["window"] = pub.get("buffer_window") or pub.get("window")
    out.pop("_custom_slots", None)   # les horaires fixes du calendrier concernent YouTube
    a, b = out["window"] if out.get("window") else ("07:00", "23:30")
    span = (int(b[:2]) * 60 + int(b[3:])) - (int(a[:2]) * 60 + int(a[3:]))
    out.setdefault("min_gap_minutes", int(0.6 * span / max(out.get("per_day") or 1, 1)))
    return out


def _publish_buffer(clip_path, meta, pub, state, res):
    """Programme le clip sur chaque compte Buffer pas encore servi, sur le prochain créneau
    Buffer libre (cadence buffer_per_day, distincte de YouTube)."""
    import buffer
    todo = [ch for ch in pub.get("buffer_channels") or [] if ch["id"] not in res.get("buffer", {})]
    if not todo:
        return
    room = {ch["id"]: buffer.QUEUE_MAX - buffer.queued(state, ch["id"]) for ch in todo}
    todo = [ch for ch in todo if room[ch["id"]] > 0]
    if not todo:
        print(f"    … Buffer : file pleine ({buffer.QUEUE_MAX} posts programmés), en attente")
        return
    try:
        if not res.get("video_url"):
            res["video_url"] = buffer.host(
                clip_path, f"{CHANNEL_SLUG}-{clip_path.parent.name}-{clip_path.name}")
    except Exception as e:
        res["buffer_error"] = str(e)[:300]
        print(f"    ✗ hébergement Vercel Blob : {str(e)[:200]}")
        return
    taken = state.setdefault("taken_buffer", [])
    when = next_slots(buffer_pub(pub), taken, 1)[0]
    text = description(meta)
    ok = False
    for ch in todo:
        try:
            pid = buffer.schedule(ch, res["video_url"], text, meta.get("title", ""), when, CATEGORY)
            res.setdefault("buffer", {})[ch["id"]] = {"id": pid, "when": when.isoformat(),
                                                     "service": ch["service"]}
            ok = True
            print(f"    ✓ Buffer {ch['service']} : programmé le {when:%d/%m %Hh%M}")
        except Exception as e:
            res.setdefault("buffer_errors", {})[ch["service"]] = str(e)[:300]
            print(f"    ✗ Buffer {ch['service']} : {str(e)[:200]}")
    if ok:
        taken.append(when.isoformat())
        state["taken_buffer"] = taken[-300:]


def publish(clip_path: Path, meta, pub, when: datetime, state, prev=None, youtube=True):
    """Publie sur chaque cible de la chaîne, sans jamais renvoyer une cible déjà servie :
    - YouTube (API gratuite) si backend youtube_api, au créneau `when` (cadence YouTube) ;
    - chaque compte de publish.buffer_channels, au prochain créneau Buffer (cadence Buffer).
    `pending` = il manque encore une cible « principale » (Buffer si la chaîne en a, sinon
    YouTube). `yt_pending` = YouTube pas encore servi (choisi ensuite parmi les meilleurs clips)."""
    res = dict(prev or {})
    for k in ("pending", "yt_pending", "sent_now"):
        res.pop(k, None)
    res.setdefault("when", when.isoformat())
    backend = pub.get("backend")
    chans = pub.get("buffer_channels") or []
    if backend not in ("youtube_api", "buffer") and not chans:
        res["queue"] = queue_manual(clip_path, meta, when)
        return res
    if backend == "youtube_api" and not res.get("youtube_id") and youtube:
        _publish_youtube(clip_path, meta, pub, when, state, res)
    yt_missing = backend == "youtube_api" and not res.get("youtube_id")
    if chans:
        _publish_buffer(clip_path, meta, pub, state, res)
        if any(ch["id"] not in res.get("buffer", {}) for ch in chans):
            res["pending"] = True
        if yt_missing:
            res["yt_pending"] = True
    elif yt_missing:
        res["pending"] = True
    res["sent_now"] = res.get("youtube_when") == when.isoformat()   # créneau YouTube consommé
    return res


def purge_published(pub, state, save):
    """Supprime les fichiers des clips déjà envoyés sur TOUTES les cibles de la chaîne
    (YouTube a la vidéo ; Buffer l'a sur Vercel Blob jusqu'à la publication).
    Le registre (titre, liens, ids) est gardé : seuls les .mp4/.ass locaux disparaissent."""
    if not pub.get("delete_after_publish", True):
        return 0
    chans = [ch["id"] for ch in pub.get("buffer_channels") or []]
    n = 0
    for c in state["clips"]:
        p = c.get("publish") or {}
        if not p or p.get("pending") or p.get("yt_pending") or p.get("dropped") or c.get("purged"):
            continue
        if pub.get("backend") == "youtube_api" and not p.get("youtube_id"):
            continue
        if any(ch not in (p.get("buffer") or {}) for ch in chans):
            continue
        if pub.get("backend") not in ("youtube_api", "buffer") and not chans:
            continue   # publication manuelle : le fichier sert encore (a_poster/)
        f = ROOT / c["file"]
        for g in (f, f.with_suffix(".ass")):
            g.unlink(missing_ok=True)
        c["purged"] = datetime.now().isoformat(timespec="seconds")
        n += 1
    if n:
        print(f"  {n} clip(s) en ligne : fichiers locaux supprimés")
        save(state)
    return n


def upload_pending(pub, state, save, youtube=True):
    """Rattrape ce qui n'est pas encore parti :
    1) Buffer : clips pas encore programmés sur un compte (file pleine), dans l'ordre ;
    2) YouTube : les MEILLEURS clips pas encore envoyés, dans la limite du quota du jour."""
    chans = pub.get("buffer_channels") or []
    if chans:
        import buffer
        buffer.cleanup(state)   # vidéos déjà publiées : on libère l'hébergement temporaire
        save(state)
    sent = 0
    if chans:
        todo = [c for c in state["clips"] if (c.get("publish") or {}).get("pending")]
        todo.sort(key=_freshness, reverse=True)   # le contenu le plus récent part en premier
        if todo:
            print(f"\n[file Buffer] {len(todo)} clip(s) à programmer, places : "
                  f"{max(buffer_room(pub, state), 0)}")
        for c in todo:
            if buffer_room(pub, state) <= 0:
                break
            p = dict(c.get("publish") or {})
            res = publish(ROOT / c["file"], c, pub, datetime.fromisoformat(p["when"]), state,
                          prev=p, youtube=False)
            if p.get("yt_pending") or (pub.get("backend") == "youtube_api"
                                      and not res.get("youtube_id")):
                res["yt_pending"] = True
            res.pop("sent_now", None)
            c["publish"] = res
            sent += bool(res.get("buffer"))
            save(state)
    if pub.get("backend") != "youtube_api" or not youtube:
        return sent
    flag = "yt_pending" if chans else "pending"
    todo = [c for c in state["clips"] if (c.get("publish") or {}).get(flag)
            and (ROOT / c["file"]).exists()]
    if not todo:
        return sent
    print(f"\n[file YouTube] {len(todo)} clip(s) en attente, quota restant : "
          f"{max(quota_left(pub, state), 0)}")
    pins = pub.get("_pins") or {}
    tz = ZoneInfo(pub.get("timezone", "Europe/Paris"))
    soon = datetime.now(tz) + timedelta(minutes=30)
    # épinglés d'abord (calendrier), puis le contenu le plus récent (au jour près), puis le score
    todo.sort(key=lambda c: (c["file"] not in pins, pins.get(c["file"], ""),
                             -int(_freshness(c) // 86400), -(c.get("score") or 0)))
    for c in todo:
        if quota_left(pub, state) <= 0:
            break
        pin = pins.get(c["file"])
        pin = datetime.fromisoformat(pin) if pin else None
        when = pin if pin and pin > soon else next_slots(pub, state["taken_slots"], 1)[0]
        p = dict(c.get("publish") or {})
        _publish_youtube(ROOT / c["file"], c, pub, when, state, p)
        if p.get("youtube_id"):
            p.pop(flag, None)
            state["taken_slots"].append(when.isoformat())
            sent += 1
        c["publish"] = p
        save(state)
    return sent
