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


def run_pipeline(agent):
    ch = channel_id(agent["id"])
    print(f"\n### Agent {agent['name']} → channels/{ch}")
    # Le pipeline d'origine, inchangé ; la publication passe par le SaaS, jamais par agent.py ici.
    subprocess.run([sys.executable, "agent.py", "--channel", ch, "--no-publish"], cwd=ROOT, check=False)


def report_clips(agent):
    d = ROOT / "channels" / channel_id(agent["id"])
    state_file, sent_file = d / "state.json", d / "reported.json"
    if not state_file.exists():
        return 0
    state = json.loads(state_file.read_text(encoding="utf-8"))
    sent = set(json.loads(sent_file.read_text(encoding="utf-8"))) if sent_file.exists() else set()
    n = 0
    for c in state.get("clips", []):
        key = f"{agent['id']}:{c['file']}"
        if key in sent:
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
            res = api("POST", "/api/engine/clips", json=body)
            print(f"  clip envoyé : {c.get('hook')!r} → {res.get('status')}")
            sent.add(key)
            n += 1
        except Exception as e:
            print(f"  ! envoi impossible : {e}")
    sent_file.write_text(json.dumps(sorted(sent)), encoding="utf-8")
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


def publish_buffer(path, clip, channel, key=None):
    """Programme le clip via Buffer (même code que Clip by Cut) : vidéo hébergée sur Vercel Blob, post dans 10 min."""
    import buffer
    from datetime import datetime, timedelta, timezone
    url = clip.get("_video_url") or buffer.host(path, path.name)
    clip["_video_url"] = url
    when = datetime.now(timezone.utc) + timedelta(minutes=10)
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
    for clip in data.get("clips", []):
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
                    pub["url"] = publish_buffer(path, clip, via_buffer)
                    pub["status"], pub["error"] = "scheduled", None
                    print(f"  programmé via Buffer ({via_buffer.get('service')}) : {pub['url']}")
                    continue
                t = api("GET", f"/api/engine/accounts/{pub['accountId']}/token")
                if t.get("via") == "buffer":
                    # Chaîne connectée dans Otto Clip via le compte Buffer de l'utilisateur.
                    ch = {"id": t["bufferChannelId"], "service": t["platform"]}
                    pub["url"] = publish_buffer(path, clip, ch, key=t["token"])
                    pub["status"], pub["error"] = "scheduled", None
                    print(f"  programmé via Buffer ({t['platform']}) : {pub['url']}")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agent")
    a = ap.parse_args()
    if len(TOKEN) < 32:
        sys.exit("ENGINE_TOKEN manquant (32 caractères minimum, même valeur que dans apps/web/.env.local).")
    agents = api("GET", "/api/engine/agents")["agents"]
    if a.agent:
        agents = [x for x in agents if x["id"] == a.agent]
    print(f"{len(agents)} agent(s) actif(s) avec des crédits.")
    for ag in agents:
        write_channel(ag)
        if a.dry_run:
            print(yaml.safe_dump(ag["config"], allow_unicode=True, sort_keys=False))
            continue
        run_pipeline(ag)
        report_clips(ag)
    if not a.dry_run:
        publish_approved()


if __name__ == "__main__":
    main()
