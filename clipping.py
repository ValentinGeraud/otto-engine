"""Soumission automatique des posts à une campagne clipping.net.

clipping.net n'a pas d'API publique : on utilise la même route que le bouton « Upload » du site
(POST /api/clipper/clips), avec TA session (cookies de connexion), stockée dans
secrets/clipping.json. Le serveur du site renouvelle lui-même la session et renvoie les
nouveaux cookies : on les réenregistre à chaque appel.

Une seule fois :
  python clipping.py --set-cookie     colle l'en-tête « cookie » d'une requête vers clipping.net
  python clipping.py --check          vérifie que la session marche (comptes reliés)

Config de la chaîne (config.yaml > publish > clipping) :
  clipping:
    campaign_id: "6ab191a2a6d24fa26606ce51"
    bounty_tag: "lex-valentino"
    platforms: [instagram]          # seules les plateformes acceptées par la campagne

Le robot soumet un post seulement quand il est VRAIMENT en ligne (Buffer « sent » avec un lien
public, ou vidéo YouTube publique), une seule fois, et note la réponse dans state.json.
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path

ROOT = Path(__file__).parent
SECRET = ROOT / "secrets" / "clipping.json"
SITE = "https://clipping.net"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " \
     "Chrome/131.0 Safari/537.36"


def _load():
    try:
        return json.loads(SECRET.read_text(encoding="utf-8")).get("cookies") or {}
    except Exception:
        return {}


def _save(cookies):
    SECRET.write_text(json.dumps({"cookies": cookies, "updated": datetime.now().isoformat()},
                                 indent=1), encoding="utf-8")


def _request(path, body=None, method=None):
    cookies = _load()
    if not cookies:
        raise RuntimeError("session clipping.net absente : lance « python clipping.py --set-cookie »")
    headers = {"User-Agent": UA, "Accept": "application/json",
               "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(SITE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, raw, set_cookies = r.status, r.read(), r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        status, raw, set_cookies = e.code, e.read(), e.headers.get_all("Set-Cookie") or []
    # session renouvelée par le site : on garde les nouveaux cookies (et on retire les effacés)
    if set_cookies:
        for line in set_cookies:
            c = SimpleCookie()
            c.load(line)
            for name, m in c.items():
                if m.value == "" or m["max-age"] == "0":
                    cookies.pop(name, None)
                else:
                    cookies[name] = m.value
        _save(cookies)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        payload = {"raw": raw.decode("utf-8", "replace")[:300]}
    return status, payload


def check():
    status, p = _request("/api/clipper/social-accounts")
    if status in (401, 403):
        raise RuntimeError("session clipping.net expirée : relance « python clipping.py --set-cookie »")
    accounts = p.get("data") or p.get("accounts") or p
    return status, accounts


def submit(url, platform, campaign_id, bounty_tag=None):
    """Soumet un post à la campagne. Renvoie (ok, message)."""
    body = {"campaignId": campaign_id, "url": url, "platform": platform,
            "bountyTag": bounty_tag, "specialUploadTag": None,
            "isBountySubmission": bool(bounty_tag), "isSpecialUpload": False, "notes": None}
    status, p = _request("/api/clipper/clips", body, "POST")
    if 200 <= status < 300 and p.get("success", True) is not False:
        return True, p.get("message") or "soumis"
    return False, f"HTTP {status} : {p.get('error') or p.get('message') or p}"[:300]


# ---------- après publication : trouver les posts en ligne et les soumettre ----------
def _buffer_links(ids):
    """{post_id: lien public} pour les posts Buffer déjà publiés."""
    import buffer
    out = {}
    for pid in ids:
        try:
            q = "query($id: PostId!) { post(input: {id: $id}) { id status externalLink } }"
            post = buffer.gql(q, {"id": pid})["post"]
            if post and post.get("status") == "sent" and post.get("externalLink"):
                out[pid] = post["externalLink"]
        except Exception as e:
            print(f"  (Buffer, post {pid} : {str(e)[:100]})")
    return out


def submit_live(cfg, state, save):
    """Soumet à la campagne chaque post publié et pas encore soumis. Renvoie le nombre soumis."""
    conf = (cfg.get("publish") or {}).get("clipping") or {}
    if not conf.get("campaign_id"):
        return 0
    allowed = set(conf.get("platforms") or ["instagram"])
    todo = []   # (clip, plateforme, id du post)
    for c in state["clips"]:
        p = c.get("publish") or {}
        done = p.setdefault("clipping", {})
        for b in (p.get("buffer") or {}).values():
            if b["service"] in allowed and b["service"] not in done:
                todo.append((c, b["service"], b["id"]))
        if "youtube" in allowed and p.get("youtube_id") and "youtube" not in done:
            when = datetime.fromisoformat(p.get("youtube_when") or p["when"])
            if when < datetime.now(timezone.utc):
                todo.append((c, "youtube", p["youtube_id"]))
    if not todo:
        return 0
    links = _buffer_links([pid for _, s, pid in todo if s != "youtube"])
    yt_ids = [pid for _, s, pid in todo if s == "youtube"]
    if yt_ids:   # tant que l'audit API n'est pas validé, les vidéos restent privées : on attend
        try:
            import publish
            items = publish._youtube().videos().list(part="status", id=",".join(yt_ids[:50])) \
                .execute().get("items", [])
            public = {v["id"] for v in items if v["status"].get("privacyStatus") == "public"}
        except Exception as e:
            print(f"  (statut YouTube indisponible : {str(e)[:100]})")
            public = set()
        todo = [t for t in todo if t[1] != "youtube" or t[2] in public]
    # posts déjà présents sur clipping.net (soumis à la main ou lors d'un passage précédent)
    try:
        status, p = _request("/api/clipper/clips")
        rows = (p.get("data") or {}).get("data") if isinstance(p.get("data"), dict) else p.get("data")
        known = {r.get("videoId") for r in rows or [] if isinstance(r, dict)}
    except Exception:
        known = set()
    sent = 0
    for c, service, pid in todo:
        url = f"https://www.youtube.com/shorts/{pid}" if service == "youtube" else links.get(pid)
        if not url:
            continue   # pas encore publié : on réessaiera au prochain passage
        vid = url.rstrip("/").split("/")[-1]
        if vid in known:
            c["publish"]["clipping"][service] = {"url": url, "ok": True, "msg": "déjà sur clipping.net",
                                                 "at": datetime.now().isoformat(timespec="seconds")}
            save(state)
            continue
        try:
            ok, msg = submit(url, service, conf["campaign_id"], conf.get("bounty_tag"))
        except Exception as e:
            print(f"  ✗ clipping.net : {str(e)[:200]}")
            return sent   # session absente ou expirée : inutile d'insister
        c["publish"]["clipping"][service] = {"url": url, "ok": ok, "msg": msg,
                                             "at": datetime.now().isoformat(timespec="seconds")}
        if not ok:   # refus (ex. PayPal manquant) : on retentera au prochain passage
            c["publish"]["clipping"].pop(service)
        print(f"  {'✓' if ok else '✗'} clipping.net {service} : {url} ({msg})")
        sent += ok
        save(state)
    return sent


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--set-cookie" in sys.argv:
        print("Colle l'en-tête « cookie » d'une requête vers clipping.net, puis Entrée :")
        raw = input().strip()
        if raw.lower().startswith("cookie:"):
            raw = raw[7:].strip()
        jar = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k.startswith("sb-") and "code-verifier" not in k:   # seule la session sert
                    jar[k] = v
        if not jar:
            sys.exit("Aucun cookie de session « sb-…-auth-token » trouvé dans ce texte.")
        SECRET.parent.mkdir(exist_ok=True)
        _save(jar)
        print(f"Session enregistrée ({', '.join(jar)}).")
    if "--check" in sys.argv or "--set-cookie" in sys.argv:
        try:
            status, acc = check()
            print(f"clipping.net répond (HTTP {status}) : {json.dumps(acc, ensure_ascii=False)[:400]}")
        except Exception as e:
            sys.exit(str(e))
