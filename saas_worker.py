"""Worker Otto Clip : relie le moteur de clipping (agent.py) au SaaS.

À chaque passage :
  1. récupère les agents actifs du SaaS (GET /api/engine/agents) ;
  2. pour chaque agent, écrit channels/saas_<id>/config.yaml puis lance le pipeline existant
     (découverte → transcription → IA → montage 9:16) SANS publication ;
  3. envoie chaque nouveau clip au SaaS (POST /api/engine/clips), qui débite les crédits ;
  4. si la publication est activée des deux côtés, publie les clips validés sur les comptes
     connectés des utilisateurs (YouTube, TikTok, Instagram) et renvoie le résultat ;
  5. met à jour les vues des clips publiés sur YouTube.

Variables d'environnement :
  OTTO_API_URL          adresse du SaaS (ex. http://localhost:3000)
  ENGINE_TOKEN          même valeur que dans apps/web/.env.local (32 caractères minimum)
  OTTO_PUBLISH_ENABLED  "true" pour publier vraiment (désactivé par défaut)
  OTTO_MEDIA_BASE_URL   adresse publique où les fichiers de clips sont servis (requis par Instagram)
  BUFFER_ENABLED        "true" pour passer par Buffer (buffer.py) au lieu des API officielles, pour les
                        comptes listés dans secrets/buffer_channels.json {"<accountId SaaS>": {"id": "...",
                        "service": "tiktok"}}. Branché mais désactivé par défaut. Il faut aussi
                        OTTO_PUBLISH_ENABLED=true et la clé BUFFER_API_KEY (+ BLOB_READ_WRITE_TOKEN).

  python saas_worker.py              # un passage complet
  python saas_worker.py --dry-run    # affiche les agents et leur config, ne lance rien
  python saas_worker.py --agent <id> # un seul agent
  python saas_worker.py --listen     # reste à l'écoute : « Lancer maintenant » depuis le SaaS (~20 s)
                                     # + un passage automatique toutes les 30 min (--every N, 0 = jamais)

Chaque passage est suivi en direct dans le SaaS (étape en cours, clips prêts), et le SaaS
affiche « Moteur en ligne » tant que ce worker l'interroge (OTTO_WORKER_ID, « pc » par défaut).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
API = os.environ.get("OTTO_API_URL", "http://localhost:3000").rstrip("/")
TOKEN = os.environ.get("ENGINE_TOKEN", "")
PUBLISH = os.environ.get("OTTO_PUBLISH_ENABLED") == "true"
MEDIA_BASE = os.environ.get("OTTO_MEDIA_BASE_URL", "").rstrip("/")
BUFFER = os.environ.get("BUFFER_ENABLED") == "true"
BUFFER_CHANNELS = ROOT / "secrets" / "buffer_channels.json"
WORKER_ID = os.environ.get("OTTO_WORKER_ID", "pc")
sys.stdout.reconfigure(encoding="utf-8")


def api(method, path, **kw):
    r = requests.request(method, f"{API}{path}", headers={"authorization": f"Bearer {TOKEN}"},
                         timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} → HTTP {r.status_code} : {r.text[:200]}")
    return r.json()


# ---------- 1-3 : production ----------
def channel_id(agent_id):
    return "saas_" + "".join(ch for ch in agent_id if ch.isalnum())[:24]


def write_channel(agent):
    """Le SaaS fournit une config au format de config.yaml ; elle est fusionnée sur la config de base."""
    cfg = json.loads(json.dumps(agent["config"]))
    cfg["publish"].pop("accounts", None)  # les comptes restent côté SaaS
    d = ROOT / "channels" / channel_id(agent["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(
        f"# Généré par saas_worker.py pour l'agent « {agent['name']} » ({agent['id']}). Ne pas modifier.\n"
        + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return d


def _state_clips(agent):
    f = ROOT / "channels" / channel_id(agent["id"]) / "state.json"
    try:
        return len(json.loads(f.read_text(encoding="utf-8")).get("clips", []))
    except Exception:
        return 0


def _step_of(line, title):
    """Traduit la sortie d'agent.py en étape lisible dans le SaaS."""
    t = f" · {title[:60]}" if title else ""
    s = line.strip()
    if s.startswith("[découverte]"):
        return "Recherche des vidéos qui décollent"
    if s.startswith("téléchargement"):
        return "Téléchargement" + t
    if s.startswith("transcription"):
        return "Transcription" + t
    if s.startswith("sélection des meilleurs moments"):
        return "Choix des meilleurs moments (IA)" + t
    if s.startswith("[") and "/" in s[:8] and "]" in s[:8]:
        return f"Montage du clip {s[1:s.index(']')]}" + t
    return None


def run_pipeline(agent, run_id=None):
    """Le pipeline d'origine (agent.py), sans publication : celle-ci passe par le SaaS.
    Avec run_id, chaque étape est renvoyée au SaaS pour être suivie en direct."""
    ch = channel_id(agent["id"])
    print(f"\n### Agent {agent['name']} → channels/{ch}", flush=True)
    before = _state_clips(agent)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, "-u", "agent.py", "--channel", ch, "--no-publish"], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                         encoding="utf-8", errors="replace")
    title, last_sent, info = "", 0.0, {"message": None}
    for line in p.stdout:
        if "Deprecated Feature" in line:
            continue
        print(line, end="", flush=True)
        s = line.strip()
        if s.startswith("=== "):
            title = s[4:]
        if s.startswith("Rien de nouveau à clipper"):
            info["message"] = "Aucune nouvelle vidéo à clipper sur les chaînes surveillées."
        if s.startswith("ERREUR sur"):
            info["message"] = s[:300]
        step = _step_of(line, title)
        # étape nouvelle, ou signe de vie toutes les 60 s pendant les étapes longues
        if run_id and (step or time.time() - last_sent > 60):
            body = {"workerId": WORKER_ID, "clips": max(0, _state_clips(agent) - before)}
            if step:
                body["step"] = step
            run_update(run_id, body)
            last_sent = time.time()
    p.wait()
    return info["message"]


def run_update(run_id, body):
    try:
        api("PATCH", f"/api/engine/runs/{run_id}", json={"workerId": WORKER_ID, **body})
    except Exception as e:
        print(f"  ! suivi du passage : {e}", flush=True)


def run_agent(agent, run_id=None):
    """Un passage complet pour un agent : production, envoi des clips au SaaS, suivi en direct."""
    write_channel(agent)
    if run_id:
        run_update(run_id, {"status": "running", "step": "Démarrage"})
    try:
        message = run_pipeline(agent, run_id)
        if run_id:
            run_update(run_id, {"step": "Envoi des clips à Otto Clip"})
        n = report_clips(agent)
        if run_id:
            run_update(run_id, {"status": "done", "step": None, "clips": n,
                                "message": None if n else (message or "Aucun nouveau clip.")})
        return n
    except Exception as e:
        if run_id:
            run_update(run_id, {"status": "failed", "step": None, "message": str(e)[:300]})
        raise


def upload_video(clip_id, path):
    """Envoie la vidéo rendue au SaaS pour qu'elle soit visible dans Otto Clip (et servie à Buffer).
    Vercel Blob en production (jeton limité à ce fichier, fourni par le SaaS), le SaaS lui-même en local."""
    t = api("GET", f"/api/engine/clips/{clip_id}/video")
    if t.get("mode") == "blob":
        with open(path, "rb") as f:
            r = requests.put(t["url"], data=f, headers=t["headers"], timeout=900)
        if r.status_code >= 400:
            raise RuntimeError(f"Vercel Blob → HTTP {r.status_code} : {r.text[:200]}")
        api("PATCH", f"/api/engine/clips/{clip_id}/video", json={"url": r.json()["url"]})
    elif t.get("mode") == "local":
        with open(path, "rb") as f:
            r = requests.put(f"{API}/api/engine/clips/{clip_id}/video", data=f, timeout=900,
                             headers={"authorization": f"Bearer {TOKEN}", "content-type": "video/mp4"})
        if r.status_code >= 400:
            raise RuntimeError(f"envoi local → HTTP {r.status_code} : {r.text[:200]}")
    else:
        raise RuntimeError(t.get("error") or "stockage vidéo indisponible")


def report_clips(agent):
    """Envoie au SaaS chaque nouveau clip, puis sa vidéo. Les vidéos pas encore envoyées
    (clips d'avant cette fonction, ou envoi raté) sont rattrapées au passage suivant."""
    d = ROOT / "channels" / channel_id(agent["id"])
    state_file, sent_file, video_file = d / "state.json", d / "reported.json", d / "uploaded.json"
    if not state_file.exists():
        return 0
    state = json.loads(state_file.read_text(encoding="utf-8"))
    sent = set(json.loads(sent_file.read_text(encoding="utf-8"))) if sent_file.exists() else set()
    uploaded = set(json.loads(video_file.read_text(encoding="utf-8"))) if video_file.exists() else set()
    n = 0
    for c in state.get("clips", []):
        key = f"{agent['id']}:{c['file']}"
        if key in sent and key in uploaded:
            continue
        info = {}
        info_file = ROOT / "work" / Path(c["file"]).parent.name / "info.json"
        if info_file.exists():
            info = json.loads(info_file.read_text(encoding="utf-8"))
        body = {"agentId": agent["id"], "engineKey": key, "sourceUrl": c["source"],
                "sourceTitle": info.get("title"), "sourceCreator": c.get("credit") or info.get("uploader"),
                "start": c["start"], "end": c["end"], "score": c.get("score"), "hook": c.get("hook"),
                "title": c.get("title"), "caption": c.get("caption"), "takeaway": c.get("takeaway"),
                "fileKey": c["file"]}
        try:
            # Idempotent côté SaaS (engineKey) : un clip déjà envoyé renvoie simplement son id.
            res = api("POST", "/api/engine/clips", json=body)
            if key not in sent:
                print(f"  clip envoyé : {c.get('hook')!r} → {res.get('status')}")
                sent.add(key)
                n += 1
        except Exception as e:
            print(f"  ! envoi impossible : {e}")
            continue
        path = ROOT / c["file"]
        if not path.is_file():
            print(f"  ! vidéo introuvable : {c['file']}")
            continue
        try:
            upload_video(res["id"], path)
            uploaded.add(key)
            print(f"  vidéo envoyée ({path.stat().st_size // 1_000_000} Mo)")
        except Exception as e:
            print(f"  ! vidéo non envoyée (nouvel essai au prochain passage) : {e}")
    sent_file.write_text(json.dumps(sorted(sent)), encoding="utf-8")
    video_file.write_text(json.dumps(sorted(uploaded)), encoding="utf-8")
    return n


# ---------- 4 : publication ----------
def hashtags_line(caption, title):
    return caption or title or ""


def publish_youtube(token, path, clip):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    yt = build("youtube", "v3", credentials=Credentials(token), cache_discovery=False)
    body = {"snippet": {"title": (clip.get("title") or clip.get("hook") or "Clip")[:100],
                        "description": hashtags_line(clip.get("caption"), clip.get("title")) + "\n\n#shorts",
                        "categoryId": "24"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), chunksize=-1, resumable=True)).execute()
    return f"https://youtube.com/shorts/{res['id']}"


def publish_tiktok(token, path, clip):
    h = {"authorization": f"Bearer {token}", "content-type": "application/json; charset=UTF-8"}
    info = requests.post("https://open.tiktokapis.com/v2/post/publish/creator_info/query/", headers=h,
                         timeout=30).json().get("data", {})
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else options[0]
    size = path.stat().st_size
    init = requests.post("https://open.tiktokapis.com/v2/post/publish/video/init/", headers=h, timeout=30, json={
        "post_info": {"title": hashtags_line(clip.get("caption"), clip.get("title"))[:2200],
                      "privacy_level": privacy},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size,
                        "total_chunk_count": 1}}).json()
    data = init.get("data") or {}
    if not data.get("upload_url"):
        raise RuntimeError(f"TikTok : {init.get('error', {}).get('message', 'initialisation refusée')}")
    with open(path, "rb") as f:
        r = requests.put(data["upload_url"], data=f, timeout=600, headers={
            "content-type": "video/mp4", "content-range": f"bytes 0-{size - 1}/{size}"})
    r.raise_for_status()
    return f"tiktok:{data.get('publish_id')} ({privacy})"


def publish_instagram(token, path, clip, ig_user_id):
    if not MEDIA_BASE:
        raise RuntimeError("Instagram récupère la vidéo par une adresse publique : définir OTTO_MEDIA_BASE_URL")
    g = "https://graph.instagram.com/v21.0"
    video_url = f"{MEDIA_BASE}/{clip['fileKey']}"
    c = requests.post(f"{g}/{ig_user_id}/media", timeout=60, data={
        "media_type": "REELS", "video_url": video_url,
        "caption": hashtags_line(clip.get("caption"), clip.get("title"))[:2200], "access_token": token}).json()
    if "id" not in c:
        raise RuntimeError(f"Instagram : {c.get('error', {}).get('message', 'conteneur refusé')}")
    for _ in range(40):  # Instagram traite la vidéo avant publication
        st = requests.get(f"{g}/{c['id']}", params={"fields": "status_code", "access_token": token},
                          timeout=30).json().get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("Instagram : traitement de la vidéo en échec")
        time.sleep(15)
    p = requests.post(f"{g}/{ig_user_id}/media_publish", timeout=60,
                      data={"creation_id": c["id"], "access_token": token}).json()
    if "id" not in p:
        raise RuntimeError(f"Instagram : {p.get('error', {}).get('message', 'publication refusée')}")
    return f"instagram:{p['id']}"


def buffer_channel(account_id):
    """Compte Buffer associé à un compte du SaaS, seulement si Buffer est activé."""
    if not BUFFER:
        return None
    try:
        return json.loads(BUFFER_CHANNELS.read_text(encoding="utf-8")).get(account_id)
    except Exception:
        return None


SLOTS_FILE = ROOT / "channels" / "buffer_slots.json"


def _ramp_today(pub_cfg, today):
    ramp = pub_cfg.get("ramp") or {}
    per_week = ramp.get("per_day_by_week") or [pub_cfg.get("per_day") or 3]
    try:
        from datetime import date
        week = max(0, (today - date.fromisoformat(ramp["start"])).days // 7)
    except Exception:
        week = 0
    return max(1, int(per_week[min(week, len(per_week) - 1)]))


def next_slot(account_id, pub_cfg):
    """Prochain créneau libre pour ce compte, au rythme de l'agent : N posts par jour (montée en
    puissance par semaine) répartis régulièrement dans la fenêtre horaire, jamais avant 10 min.
    Les créneaux déjà pris sont gardés dans channels/buffer_slots.json."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(pub_cfg.get("timezone") or "Europe/Paris")
    start_s, end_s = (pub_cfg.get("window") or ["09:00", "21:30"])[:2]
    try:
        taken = json.loads(SLOTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        taken = {}
    now = datetime.now(timezone.utc)
    used = {t for t in taken.get(account_id, []) if datetime.fromisoformat(t) > now - timedelta(days=1)}
    earliest = now + timedelta(minutes=10)
    day = earliest.astimezone(tz).date()
    for _ in range(60):
        a = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time(), tz)
        b = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time(), tz)
        n = _ramp_today(pub_cfg, day)
        step = (b - a) / n
        for i in range(n):
            slot = (a + step * i + step / 2).astimezone(timezone.utc).replace(second=0, microsecond=0)
            key = slot.isoformat()
            if slot >= earliest and key not in used:
                taken[account_id] = sorted(used | {key})
                SLOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
                SLOTS_FILE.write_text(json.dumps(taken, indent=1), encoding="utf-8")
                return slot
        day += timedelta(days=1)
    return earliest


def publish_buffer(path, clip, channel, key=None, when=None):
    """Programme le clip via Buffer (même code que Clip by Cut) au créneau `when` (sinon dans 10 min).
    La vidéo est servie à Buffer depuis Vercel Blob."""
    import buffer
    from datetime import datetime, timedelta, timezone
    # La vidéo est déjà sur Vercel Blob (envoyée pour l'aperçu dans Otto Clip) : Buffer la récupère là.
    hosted = clip.get("videoUrl") if str(clip.get("videoUrl") or "").startswith("https://") else None
    url = clip.get("_video_url") or hosted or buffer.host(path, path.name)
    clip["_video_url"] = url
    when = when or datetime.now(timezone.utc) + timedelta(minutes=10)
    post = buffer.schedule(channel, url, hashtags_line(clip.get("caption"), clip.get("title")),
                           clip.get("title") or "", when, key=key)
    return f"buffer:{post}"


def publish_approved():
    if not PUBLISH:
        print("\nPublication désactivée (OTTO_PUBLISH_ENABLED ≠ true) : les clips restent dans le SaaS.")
        return
    data = api("GET", "/api/engine/clips")
    if not data.get("publishEnabled"):
        print("\nPublication désactivée côté SaaS.")
        return
    try:
        cfgs = {a["id"]: a["config"].get("publish") or {} for a in api("GET", "/api/engine/agents")["agents"]}
    except Exception:
        cfgs = {}
    for clip in data.get("clips", []):
        pub_cfg = cfgs.get(clip.get("agentId")) or {}
        path = ROOT / (clip.get("fileKey") or "")
        pubs = clip.get("publications") or []
        if not path.is_file():
            print(f"  ! fichier introuvable pour le clip {clip['id']}")
            continue
        for pub in pubs:
            if pub["status"] in ("published", "scheduled"):
                continue
            try:
                via_buffer = buffer_channel(pub["accountId"])
                if via_buffer:
                    when = next_slot(pub["accountId"], pub_cfg)
                    pub["url"] = publish_buffer(path, clip, via_buffer, when=when)
                    pub["status"], pub["error"], pub["scheduledAt"] = "scheduled", None, when.isoformat()
                    print(f"  programmé via Buffer ({via_buffer.get('service')}) : {pub['url']}")
                    continue
                t = api("GET", f"/api/engine/accounts/{pub['accountId']}/token")
                if t.get("via") == "buffer":
                    # Chaîne connectée dans Otto Clip via le compte Buffer de l'utilisateur.
                    ch = {"id": t["bufferChannelId"], "service": t["platform"]}
                    when = next_slot(pub["accountId"], pub_cfg)
                    pub["url"] = publish_buffer(path, clip, ch, key=t["token"], when=when)
                    pub["status"], pub["error"], pub["scheduledAt"] = "scheduled", None, when.isoformat()
                    print(f"  programmé via Buffer ({t['platform']}) le {when.astimezone().strftime('%d/%m %H:%M')} : {pub['url']}")
                    continue
                if t["platform"] == "youtube":
                    pub["url"] = publish_youtube(t["token"], path, clip)
                elif t["platform"] == "tiktok":
                    pub["url"] = publish_tiktok(t["token"], path, clip)
                elif t["platform"] == "instagram":
                    pub["url"] = publish_instagram(t["token"], path, clip, t["externalId"])
                pub["status"], pub["error"] = "published", None
                print(f"  publié sur {pub['platform']} : {pub['url']}")
            except Exception as e:
                pub["status"], pub["error"] = "failed", str(e)[:300]
                print(f"  ! {pub['platform']} : {e}")
        done = [p for p in pubs if p["status"] in ("published", "scheduled")]
        status = ("published" if any(p["status"] == "published" for p in pubs) else "scheduled") if done             else ("failed" if pubs else "approved")
        api("PATCH", f"/api/engine/clips/{clip['id']}", json={"status": status, "publications": pubs})


def scheduled_pass(only=None, dry_run=False):
    """Passage automatique : tous les agents actifs (ou un seul), chacun enregistré comme passage dans le SaaS."""
    agents = api("GET", "/api/engine/agents")["agents"]
    if only:
        agents = [x for x in agents if x["id"] == only]
    print(f"{len(agents)} agent(s) actif(s) avec des crédits.", flush=True)
    for ag in agents:
        if dry_run:
            write_channel(ag)
            print(yaml.safe_dump(ag["config"], allow_unicode=True, sort_keys=False))
            continue
        try:
            run_id = api("POST", "/api/engine/runs", json={"workerId": WORKER_ID, "agentId": ag["id"]}).get("runId")
        except Exception as e:
            print(f"  ! passage non enregistré dans le SaaS : {e}", flush=True)
            run_id = None
        try:
            run_agent(ag, run_id)
        except Exception as e:
            print(f"  ! agent {ag['name']} : {e}", flush=True)
    if not dry_run:
        publish_approved()


def requested_runs():
    """Passages demandés depuis le SaaS (« Lancer maintenant ») ; l'appel sert aussi de signe de vie."""
    return api("GET", f"/api/engine/runs?worker={WORKER_ID}").get("runs", [])


def listen(every_minutes):
    """Reste à l'écoute : un « Lancer maintenant » démarre en ~20 s, et un passage complet toutes les N minutes."""
    print(f"À l'écoute de {API} (moteur « {WORKER_ID} ») : demandes toutes les 20 s, "
          f"passage automatique toutes les {every_minutes} min. Ctrl+C pour arrêter.", flush=True)
    next_pass = time.time() + every_minutes * 60 if every_minutes else float("inf")
    while True:
        try:
            for r in requested_runs():
                print(f"\n>>> Lancé depuis Otto Clip : {r['agent']['name']}", flush=True)
                try:
                    run_agent(r["agent"], r["runId"])
                except Exception as e:
                    print(f"  ! {e}", flush=True)
                publish_approved()
            if time.time() >= next_pass:
                print(f"\n===== {time.strftime('%Y-%m-%d %H:%M')} · passage automatique =====", flush=True)
                scheduled_pass()
                next_pass = time.time() + every_minutes * 60
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  ! {str(e)[:300]} (nouvel essai dans 20 s)", flush=True)
        time.sleep(20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agent")
    ap.add_argument("--listen", action="store_true", help="reste à l'écoute des « Lancer maintenant » du SaaS")
    ap.add_argument("--every", type=int, default=30, help="avec --listen : passage automatique toutes les N min (0 = jamais)")
    a = ap.parse_args()
    if len(TOKEN) < 32:
        sys.exit("ENGINE_TOKEN manquant (32 caractères minimum, même valeur que dans apps/web/.env.local).")
    if a.listen:
        try:
            listen(a.every)
        except KeyboardInterrupt:
            print("\nArrêt du moteur.")
        return
    scheduled_pass(a.agent, a.dry_run)


if __name__ == "__main__":
    main()
