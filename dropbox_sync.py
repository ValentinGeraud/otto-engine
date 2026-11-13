"""Télécharge tout seul les nouveaux streams d'une campagne depuis son dossier Dropbox partagé.

Pas de compte Dropbox ni d'API : la page publique du dossier contient la liste des fichiers
(données « prefetch » encodées en base64), avec un lien direct par fichier (?dl=1).

Config de la chaîne (config.yaml) :
  dropbox:
    folder: "https://www.dropbox.com/scl/fo/.../Streams?rlkey=..."   # sous-dossier Streams
    match: "lexvalentino|Lex-Valentino"   # ne prendre que ces fichiers (regex, insensible à la casse)
    keep: 2                # au plus 2 streams non traités sur le disque (3 à 10 Go chacun)
    min_free_gb: 30        # ne télécharge pas si le disque a moins de 30 Go libres

  python dropbox_sync.py --channel lex_valentino          # télécharge ce qui manque
  python dropbox_sync.py --channel lex_valentino --list   # liste seulement
"""
import base64
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " \
     "Chrome/131.0 Safari/537.36"
URL_RE = rb"https://www\.dropbox\.com/scl/fo/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-./%()+,]+?" \
         rb"\.(?:mp4|mov|mkv|webm)\?rlkey=[a-z0-9]+"


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:60]


def list_files(folder):
    """[(nom, lien direct)] des vidéos du dossier partagé, le plus récent d'abord."""
    req = urllib.request.Request(folder, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    found = {}
    for m in re.finditer(r'registerStreamedPrefetch\(\s*"[^"]*"\s*,\s*"([^"]*)"', html):
        try:
            data = base64.b64decode(m.group(1))
        except Exception:
            continue
        for u in re.findall(URL_RE, data):
            u = u.decode()
            name = urllib.request.unquote(u.split("?")[0].rsplit("/", 1)[-1])
            found.setdefault(name, u.split("&")[0] + "&dl=1")
    if not found:
        raise RuntimeError("liste Dropbox vide : la page a peut-être changé de format")

    def date(n):
        d = re.match(r"\d{4}-\d{2}-\d{2}", n)
        return d.group(0) if d else "0000"
    return sorted(found.items(), key=lambda kv: (date(kv[0]), kv[0]), reverse=True)


def _download(url, dest):
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0, done = time.time(), 0
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while chunk := r.read(8 << 20):
            f.write(chunk)
            done += len(chunk)
            if done % (512 << 20) < (8 << 20):
                print(f"    … {done / 1e9:.1f}/{total / 1e9:.1f} Go", flush=True)
    if total and done != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"téléchargement incomplet ({done}/{total} octets)")
    part.replace(dest)
    print(f"  ✓ {dest.name} ({done / 1e9:.1f} Go en {(time.time() - t0) / 60:.0f} min)")


def sync(cfg, state, dry=False):
    """Télécharge les streams pas encore traités, du plus récent au plus ancien, sans dépasser
    `keep` fichiers en attente sur le disque. Renvoie le nombre de fichiers téléchargés."""
    conf = cfg.get("dropbox") or {}
    if not conf.get("folder") or not cfg.get("sources_dir"):
        return 0
    d = Path(cfg["sources_dir"])
    d = d if d.is_absolute() else ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("*.part"):   # téléchargement interrompu au passage précédent
        p.unlink(missing_ok=True)
    done = set(state.get("done_sources") or [])
    skipped = set(state.get("dropbox_skipped") or []) | set(conf.get("skip") or [])
    match = re.compile(conf.get("match", "."), re.I)
    files = list_files(conf["folder"])
    todo = [(n, u) for n, u in files if match.search(n)
            and _slug(Path(n).stem) not in done and n not in skipped]
    waiting = [p for p in d.glob("*") if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")
               and _slug(p.stem) not in done]
    print(f"[Dropbox] {len(files)} fichier(s) dans le dossier, {len(todo)} pas encore clippé(s), "
          f"{len(waiting)} déjà sur le disque")
    if dry:
        for n, _ in todo:
            print(f"    {n}")
        return 0
    have = {p.name for p in waiting}
    room = conf.get("keep", 2) - len(waiting)
    n_dl = 0
    for name, url in todo:
        if room <= 0:
            break
        if name in have:
            continue
        free = shutil.disk_usage(d).free / 1e9
        if free < conf.get("min_free_gb", 30):
            print(f"  (disque presque plein : {free:.0f} Go libres, téléchargement reporté)")
            break
        print(f"  téléchargement Dropbox : {name}")
        try:
            _download(url, d / name)
            n_dl += 1
            room -= 1
        except Exception as e:
            print(f"  ✗ {name} : {str(e)[:200]}")
    return n_dl


if __name__ == "__main__":
    import argparse
    import json

    import channels
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    cfg = channels.load(a.channel)
    st = json.loads(cfg["_state"].read_text(encoding="utf-8")) if cfg["_state"].exists() else {}
    sync(cfg, st, dry=a.list)
