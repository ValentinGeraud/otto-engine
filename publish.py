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
MAX_HASHTAGS = 8   # YouTube affiche les 3 premiers au-dessus du titre ; au-delà de 60 il les ignore tous


def configure(cfg):
    """Bascule sur une chaîne (voir channels.py) : dossiers, jeton YouTube, langue."""
    global QUEUE, TOKEN, CLIENT_SECRET, LANG, CATEGORY, CHANNEL_ID, HASHTAGS, _yt
    QUEUE, TOKEN, CLIENT_SECRET = cfg["_queue"], cfg["_token"], cfg["_client_secret"]
    CHANNEL_ID = (cfg.get("publish") or {}).get("youtube_channel_id", "")
    LANG = cfg.get("language", "fr")
    CATEGORY = str((cfg.get("publish") or {}).get("category_id", 22))
    HASHTAGS = (cfg.get("publish") or {}).get("hashtags") or DEFAULT_HASHTAGS.get(LANG, [])
    _yt = None


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
    for t in ["shorts", *found, meta.get("credit", ""), *HASHTAGS]:
        t = _tag(t)
        if t and not t.isdigit() and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    return text, tags[:MAX_HASHTAGS]


def description(meta, extra=""):
    """Description complète : texte, ajouts éventuels, puis la ligne de hashtags."""
    text, tags = split_hashtags(meta)
    return f"{text}{extra}\n\n" + " ".join("#" + t for t in tags)


def day_slots(pub):
    """Heures de publication d'une journée : liste fixe, ou `per_day` réparties sur la plage."""
    n = pub.get("per_day")
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
    taken = set(taken)
    while len(slots) < count:
        for hm in day_slots(pub):
            h, m = map(int, hm.split(":"))
            dt = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if dt > now + timedelta(minutes=30) and dt.isoformat() not in taken \
                    and dt not in slots:
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


def quota_left(pub, state):
    used = state.setdefault("yt_uploads", {}).get(_quota_day(), 0)
    chan = min(pub.get("api_uploads_per_day", 6), ramp_today(pub)) - used
    return min(chan, pub.get("project_uploads_per_day", 6) - _project_used())


class QuotaExceeded(Exception):
    pass


def publish(clip_path: Path, meta, pub, when: datetime, state):
    result = {"when": when.isoformat()}
    if pub.get("backend") != "youtube_api":
        result["queue"] = queue_manual(clip_path, meta, when)
        return result
    if quota_left(pub, state) <= 0:
        result["pending"] = True
        print(f"    … quota API du jour atteint : en file d'attente")
        return result
    try:
        result["youtube_id"] = upload_youtube(clip_path, meta, when, pub.get("youtube_channel", ""))
        d = state["yt_uploads"]
        d[_quota_day()] = d.get(_quota_day(), 0) + 1
        _project_set(_project_used() + 1)
    except Exception as e:
        if "quotaExceeded" in str(e):  # quota du projet épuisé : toutes ses chaînes attendent
            _project_set(10**6)
        if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
            state["yt_uploads"][_quota_day()] = 10**6  # bloqué jusqu'à demain
        result["pending"] = True
        result["youtube_error"] = str(e)[:300]
        print(f"    ✗ YouTube : {str(e)[:200]}")
    return result


def upload_pending(pub, state, save):
    """Envoie les clips en attente (quota) sur les prochains créneaux libres."""
    pending = [c for c in state["clips"] if (c.get("publish") or {}).get("pending")]
    if not pending:
        return 0
    print("\n" f"[file d'attente] {len(pending)} clip(s) en attente, quota restant : "
          f"{max(quota_left(pub, state), 0)}")
    sent = 0
    for c in pending:
        if quota_left(pub, state) <= 0:
            break
        when = next_slots(pub, state["taken_slots"], 1)[0]
        res = publish(ROOT / c["file"], c, pub, when, state)
        if res.get("youtube_id"):
            c["publish"] = res
            state["taken_slots"].append(when.isoformat())
            sent += 1
        save(state)
    return sent
