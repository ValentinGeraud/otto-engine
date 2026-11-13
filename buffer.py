"""Publication automatique via Buffer (plan Free : 3 comptes, 10 posts programmés par compte).

L'API Buffer (GraphQL, https://api.buffer.com) n'accepte pas d'envoi de fichier : la vidéo doit
être à une URL HTTPS publique jusqu'à sa publication. On l'héberge temporairement sur
Vercel Blob (gratuit sur le plan Hobby), puis on la supprime une fois le post publié.

Secrets (jamais dans le code) : secrets/buffer.json
  {"api_key": "...", "blob_token": "vercel_blob_rw_..."}
ou variables d'environnement BUFFER_API_KEY et BLOB_READ_WRITE_TOKEN.

Config de la chaîne (config.yaml > publish) :
  backend: buffer
  buffer_channels:            # ids donnés par : python buffer.py --channels
    - {id: "...", service: instagram}
    - {id: "...", service: tiktok}
    - {id: "...", service: youtube}
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SECRETS = ROOT / "secrets" / "buffer.json"
API = "https://api.buffer.com"
BLOB = "https://blob.vercel-storage.com"
QUEUE_MAX = 10   # plan Free : 10 posts programmés par compte
KEY_NAME = "api_key"   # clé du compte Buffer utilisé (publish.buffer_key) : api_key, api_key_gta…


def _secret(name, env):
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        return json.loads(SECRETS.read_text(encoding="utf-8")).get(name, "")
    except Exception:
        return ""


def _http(url, data=None, headers=None, method=None, timeout=300):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code == 429 and attempt < 3:   # limite de débit : on attend ce que le serveur demande
                time.sleep(min(int(e.headers.get("Retry-After") or 30), 120))
                continue
            raise RuntimeError(f"HTTP {e.code} sur {url.split('?')[0]} : {body}")


def gql(query, variables=None, key=None):
    """key : jeton Buffer de l'utilisateur (Otto Clip) ; sinon la clé du propriétaire (secrets / env)."""
    key = key or _secret(KEY_NAME, "BUFFER_API_KEY" if KEY_NAME == "api_key" else "")
    if not key:
        raise RuntimeError("Clé API Buffer manquante (secrets/buffer.json > api_key)")
    out = _http(API, json.dumps({"query": query, "variables": variables or {}}).encode(),
                {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    if out.get("errors"):
        raise RuntimeError(f"Buffer : {out['errors'][0].get('message')}")
    return out["data"]


# ---------- hébergement temporaire (Vercel Blob) ----------
def host(path: Path, name: str) -> str:
    token = _secret("blob_token", "BLOB_READ_WRITE_TOKEN")
    if not token:
        raise RuntimeError("Jeton Vercel Blob manquant (secrets/buffer.json > blob_token)")
    out = _http(f"{BLOB}/clips/{name}", path.read_bytes(),
                {"Authorization": f"Bearer {token}", "x-api-version": "7",
                 "x-content-type": "video/mp4", "x-add-random-suffix": "1"}, method="PUT")
    return out["url"]


def unhost(urls):
    token = _secret("blob_token", "BLOB_READ_WRITE_TOKEN")
    if urls and token:
        _http(f"{BLOB}/delete", json.dumps({"urls": list(urls)}).encode(),
              {"Authorization": f"Bearer {token}", "x-api-version": "7",
               "Content-Type": "application/json"}, method="POST")


# ---------- Buffer ----------
def channels():
    orgs = gql("query { account { organizations { id name } } }")["account"]["organizations"]
    out = []
    for o in orgs:
        q = """query($org: OrganizationId!) { channels(input: {organizationId: $org}) {
                 id name displayName service isQueuePaused isDisconnected } }"""
        out += gql(q, {"org": o["id"]})["channels"]
    return out


CREATE = """mutation($input: CreatePostInput!) { createPost(input: $input) {
  ... on PostActionSuccess { post { id dueAt } }
  ... on MutationError { message } } }"""


def schedule(channel, video_url, text, title, when: datetime, category="24", key=None):
    meta = {}
    service = channel["service"]
    if service == "instagram":
        meta = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    elif service == "youtube":
        meta = {"youtube": {"title": title[:100], "categoryId": str(category)}}
    elif service == "tiktok":
        meta = {"tiktok": {"title": title[:90]}}
    inp = {"channelId": channel["id"], "text": text, "schedulingType": "automatic",
           "mode": "customScheduled", "needsApproval": False,
           "dueAt": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
           "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": 1500}}}]}
    if meta:
        inp["metadata"] = meta
    res = gql(CREATE, {"input": inp}, key)["createPost"]
    if "post" not in res:
        raise RuntimeError(res.get("message") or "refusé par Buffer")
    return res["post"]["id"]


def _when(b):
    return datetime.fromisoformat(b["when"]).astimezone(timezone.utc)


def queued(state, channel_id):
    """Posts encore programmés (pas encore publiés) sur ce compte Buffer."""
    now = datetime.now(timezone.utc)
    return sum(1 for c in state["clips"]
               if (b := ((c.get("publish") or {}).get("buffer") or {}).get(channel_id))
               and _when(b) > now)


def cleanup(state):
    """Supprime de Vercel Blob les vidéos publiées partout depuis plus de 12 h.
    Si un compte Buffer attend encore ce clip, la vidéo sera simplement ré-hébergée."""
    limit = datetime.now(timezone.utc) - timedelta(hours=12)
    old = []
    for c in state["clips"]:
        p = c.get("publish") or {}
        posts = list((p.get("buffer") or {}).values())
        if p.get("video_url") and posts and max(_when(b) for b in posts) < limit:
            old.append(c)
    if old:
        try:
            unhost([c["publish"]["video_url"] for c in old])
            for c in old:
                c["publish"].pop("video_url")
            print(f"  Vercel Blob : {len(old)} vidéo(s) déjà publiée(s) supprimée(s)")
        except Exception as e:
            print(f"  (nettoyage Vercel Blob reporté : {str(e)[:120]})")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--channels" in sys.argv:
        for ch in channels():
            flag = " (DÉCONNECTÉ)" if ch.get("isDisconnected") else ""
            print(f"- {{id: \"{ch['id']}\", service: {ch['service']}}}   # "
                  f"{ch.get('displayName') or ch['name']}{flag}")
    else:
        print(__doc__)
