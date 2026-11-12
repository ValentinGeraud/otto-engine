"""Analytics YouTube : vues, likes et commentaires de chaque Short publié.

  python analytics.py      met à jour analytics.json (1 unité de quota par lot de 50 vidéos)
  python analytics.py --channel es_historias   idem pour une chaîne secondaire
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
FILE = ROOT / "analytics.json"
STATE = ROOT / "state.json"


def load(file=None):
    f = Path(file or FILE)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"videos": {}, "daily": {}, "updated": None}


def refresh_channel(channel):
    """Met à jour les vues d'une chaîne secondaire avec son propre jeton YouTube."""
    import channels
    import publish
    cfg = channels.load(channel)
    publish.configure(cfg)
    try:
        return refresh(cfg["_state"], cfg["_analytics"])
    finally:  # rétablit la chaîne principale (jeton, dossiers) pour le reste du processus
        publish.configure(channels.load(None))


def refresh(state_path=None, file=None):
    import publish  # import tardif : nécessite le jeton YouTube

    state_path, file = Path(state_path or STATE), Path(file or FILE)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() \
        else {"clips": []}
    ids = [c["publish"]["youtube_id"] for c in state.get("clips", [])
           if (c.get("publish") or {}).get("youtube_id")]
    data = load(file)
    if not ids:
        return data
    yt = publish._youtube()
    now = datetime.now().isoformat(timespec="minutes")
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="statistics,status", id=",".join(ids[i:i + 50])).execute()
        for it in resp.get("items", []):
            s = it.get("statistics", {})
            v = data["videos"].setdefault(it["id"], {"history": []})
            v.update(views=int(s.get("viewCount", 0)), likes=int(s.get("likeCount", 0)),
                     comments=int(s.get("commentCount", 0)),
                     privacy=it.get("status", {}).get("privacyStatus"), at=now)
            v["history"] = (v["history"] + [[now, v["views"]]])[-200:]
    total = sum(v.get("views", 0) for v in data["videos"].values())
    data["daily"][datetime.now().strftime("%Y-%m-%d")] = total
    data["updated"] = now
    file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def summary(file=None):
    """Résumé pour les tableaux de bord : total, gain sur 24 h, courbe par jour."""
    data = load(file)
    vids = data["videos"]
    days = sorted(data["daily"].items())[-30:]
    total = sum(v.get("views", 0) for v in vids.values())
    prev = days[-2][1] if len(days) >= 2 else 0
    return {
        "total_views": total,
        "total_likes": sum(v.get("likes", 0) for v in vids.values()),
        "total_comments": sum(v.get("comments", 0) for v in vids.values()),
        "views_24h": total - prev if days else 0,
        "daily": [{"day": d, "views": n} for d, n in days],
        "per_video": {k: {"views": v.get("views", 0), "likes": v.get("likes", 0),
                          "privacy": v.get("privacy")} for k, v in vids.items()},
        "updated": data["updated"],
    }


def use_channel(channel):
    """Pointe vers une chaîne secondaire (channels/<id>/) au lieu de la principale."""
    global FILE, STATE
    import channels
    import publish
    cfg = channels.load(channel)
    FILE, STATE = cfg["_analytics"], cfg["_state"]
    publish.configure(cfg)


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel")
    ch = ap.parse_args().channel
    if ch:
        use_channel(ch)
    d = refresh()
    print(json.dumps(summary(), ensure_ascii=False, indent=1)[:800])
