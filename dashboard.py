"""Tableau de bord multi-chaînes : vue d'ensemble (vues de toutes les chaînes, ce qui va
sortir), puis le détail de chaque chaîne, de la découverte jusqu'au post.

  python dashboard.py         puis ouvre http://localhost:8787
"""
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import analytics
import channels
import publish

ROOT = Path(__file__).parent
LOG = ROOT / "run.log"
STATE = ROOT / "state.json"
THUMBS = ROOT / "work" / "_thumbs"
PORT = int(__import__("os").environ.get("DASH_PORT", 8787))
sys.stdout.reconfigure(encoding="utf-8")

STEPS = ["download", "transcribe", "select", "render", "publish"]


def parse_log(LOG=LOG):
    """Reconstruit l'avancement du passage en cours à partir de run.log."""
    if not LOG.exists():
        return {"running": False, "discovery": [], "sources": [], "log": []}
    lines = [l.rstrip() for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines()
             if "Deprecated" not in l and "warnings.warn" not in l]
    discovery, sources, cur = [], [], None
    finished = any(l.startswith("EXIT") or l.startswith("Terminé") for l in lines)
    for l in lines:
        m = re.match(r"\s+(\d+) vues/h\s+(\w+)\s+(.*)", l)
        if m and cur is None:
            discovery.append({"vph": int(m[1]), "platform": m[2], "title": m[3].strip()})
            continue
        if l.startswith("=== "):
            cur = {"title": l[4:], "step": "download", "clips": [], "error": None}
            sources.append(cur)
        elif cur is None:
            continue
        elif "transcription" in l:
            cur["step"] = "transcribe"
        elif "sélection" in l:
            cur["step"] = "select"
        elif m := re.match(r"\s+\[(\d+)/(\d+)\]\s+(\d+)s→(\d+)s score (\S+) \| (.*)", l):
            cur["step"] = "render"
            cur["total"] = int(m[2])
            cur["clips"].append({"n": int(m[1]), "start": int(m[3]), "end": int(m[4]),
                                 "score": m[5], "hook": m[6], "status": "montage"})
        elif "✓ YouTube" in l and cur["clips"]:
            cur["clips"][-1]["status"] = "programmé"
            u = re.search(r"https://youtu\.be/\S+", l)
            cur["clips"][-1]["url"] = u[0] if u else None
            cur["step"] = "publish"
        elif "quota API du jour" in l and cur["clips"]:
            cur["clips"][-1]["status"] = "en file"
            cur["step"] = "publish"
        elif "✗ YouTube" in l and cur["clips"]:
            cur["clips"][-1]["status"] = "erreur"
            cur["clips"][-1]["error"] = l.split("✗ YouTube :", 1)[-1].strip()
        elif "ERREUR" in l:
            cur["error"] = l.strip()
    # un clip "montage" suivi d'un autre clip est terminé
    for s in sources:
        for c in s["clips"][:-1]:
            if c["status"] == "montage":
                c["status"] = "monté"
        if s is not sources[-1] or finished:
            s["done"] = True
            for c in s["clips"]:
                if c["status"] == "montage":
                    c["status"] = "monté"
    return {"running": not finished and bool(lines), "finished": finished,
            "discovery": discovery[:10], "sources": sources, "log": lines[-14:],
            "updated": datetime.fromtimestamp(LOG.stat().st_mtime).strftime("%H:%M:%S")}


FLAGS = {"fr": "🇫🇷", "en": "🇺🇸", "es": "🇪🇸"}


def _chan(ch):
    """Config, état et analytics d'une chaîne (ch = '' ou None pour la principale)."""
    cfg = channels.load(ch or None)
    st = cfg["_state"]
    state = json.loads(st.read_text(encoding="utf-8")) if st.exists() else \
        {"clips": [], "taken_slots": []}
    return cfg, state, analytics.summary(cfg["_analytics"])


def _upcoming(cfg, state, limit=20):
    """Ce qui va sortir : programmés dans le futur, puis la file d'attente."""
    now = datetime.now(timezone.utc)
    out = []
    for c in state.get("clips", []):
        p = c.get("publish") or {}
        when = p.get("when")
        if p.get("youtube_id") and when and datetime.fromisoformat(when) > now:
            out.append({"when": when, "status": "programmé"})
        elif p.get("pending"):
            out.append({"when": None, "status": "en file"})
        else:
            continue
        out[-1].update(hook=c.get("hook"), title=c.get("title"), file=c.get("file"),
                       url=f"https://youtu.be/{p['youtube_id']}" if p.get("youtube_id") else None,
                       channel=cfg["_id"])
    out.sort(key=lambda x: (x["when"] is None, x["when"] or ""))
    return out[:limit]


def overview():
    """Toutes les chaînes : vues, ce qui sort, courbe cumulée."""
    rows, daily, upcoming = [], {}, []
    for ch in [""] + channels.available():
        cfg, state, an = _chan(ch)
        pub = cfg["publish"]
        clips = state.get("clips", [])
        up = _upcoming(cfg, state)
        nxt = next((u for u in up if u["when"]), None)
        for d in an["daily"]:
            daily[d["day"]] = daily.get(d["day"], 0) + d["views"]
        for u in up:
            u["channel_name"] = pub.get("youtube_channel")
        upcoming += up
        rows.append({
            "id": ch, "name": pub.get("youtube_channel"), "lang": cfg.get("language"),
            "flag": FLAGS.get(cfg.get("language"), "🌐"), "niche": cfg.get("niche"),
            "connected": cfg["_token"].exists(),
            "views": an["total_views"], "views_24h": an["views_24h"],
            "likes": an["total_likes"], "updated": an["updated"],
            "made": len(clips),
            "scheduled": sum(bool((c.get("publish") or {}).get("youtube_id")) for c in clips),
            "pending": sum(bool((c.get("publish") or {}).get("pending")) for c in clips),
            "next": nxt,
            "running": parse_log(cfg["_base"] / "run.log").get("running", False),
        })
    upcoming.sort(key=lambda x: (x["when"] is None, x["when"] or ""))
    return {
        "channels": rows,
        "total": {k: sum(r[k] for r in rows) for k in
                  ("views", "views_24h", "likes", "made", "scheduled", "pending")},
        "daily": [{"day": d, "views": n} for d, n in sorted(daily.items())[-30:]],
        "upcoming": upcoming[:30],
    }


def status(ch=""):
    cfg, state, an = _chan(ch)
    pub = cfg.get("publish") or {}
    clips = []
    for c in reversed(state.get("clips", [])):
        p = c.get("publish") or {}
        st = ("programmé" if p.get("youtube_id") else "en file" if p.get("pending")
              else "erreur" if p.get("youtube_error") else "monté")
        clips.append({"file": c.get("file"), "hook": c.get("hook"), "title": c.get("title"),
                      "score": c.get("score"), "takeaway": c.get("takeaway"),
                      "why": c.get("why"), "source": c.get("source"), "status": st,
                      "when": p.get("when"), "url": f"https://youtu.be/{p['youtube_id']}"
                      if p.get("youtube_id") else None, "error": p.get("youtube_error"),
                      "views": an["per_video"].get(p.get("youtube_id") or "", {}).get("views"),
                      "likes": an["per_video"].get(p.get("youtube_id") or "", {}).get("likes")})
    used = state.get("yt_uploads", {}).get(publish._quota_day(), 0)
    nxt = publish.next_slots(pub, state.get("taken_slots", []), 1)[0] if pub else None
    return {
        "run": parse_log(cfg["_base"] / "run.log"),
        "analytics": {k: v for k, v in an.items() if k != "per_video"},
        "upcoming": _upcoming(cfg, state),
        "clips": clips,
        "stats": {
            "made": len(clips),
            "scheduled": sum(c["status"] == "programmé" for c in clips),
            "pending": sum(c["status"] == "en file" for c in clips),
            "quota_used": min(used, 999),
            "quota_max": min(pub.get("api_uploads_per_day", 6), publish.ramp_today(pub)),
            "ramp_today": publish.ramp_today(pub),
            "next_slot": nxt.strftime("%d/%m %Hh%M") if nxt else None,
            "channel": pub.get("youtube_channel"),
            "lang": cfg.get("language"),
            "connected": cfg["_token"].exists(),
        },
    }


def refresh_loop(every=1800):
    """Met à jour les vues des chaînes secondaires (la principale est faite par hq_sync)."""
    while True:
        for ch in channels.available():
            cfg = channels.load(ch)
            if not cfg["_token"].exists():
                continue
            try:
                analytics.refresh_channel(ch)
            except Exception as e:
                print(f"[analytics {ch}] {str(e)[:120]}")
        time.sleep(every)


def thumb(rel):
    src = (ROOT / rel).resolve()
    if ROOT.resolve() not in src.parents or not src.exists():
        return None
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / (re.sub(r"[^\w]+", "_", rel) + ".jpg")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(src),
                        "-frames:v", "1", "-vf", "scale=270:-2", str(out)])
    return out if out.exists() else None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, (ROOT / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/overview":
            try:
                data = overview()
            except Exception as e:
                data = {"error": str(e)}
            return self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json")
        if u.path == "/api/status":
            try:
                data = status(parse_qs(u.query).get("ch", [""])[0])
            except Exception as e:
                data = {"error": str(e)}
            return self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json")
        if u.path in ("/thumb", "/video"):
            rel = unquote(parse_qs(u.query).get("f", [""])[0])
            if u.path == "/thumb":
                t = thumb(rel)
                return self._send(200, t.read_bytes(), "image/jpeg") if t else self._send(404, b"", "text/plain")
            src = (ROOT / rel).resolve()
            if ROOT.resolve() in src.parents and src.exists() and src.suffix == ".mp4":
                return self._send(200, src.read_bytes(), "video/mp4")
        if u.path == "/logo":
            return self._send(200, (ROOT / "branding" / "photo_profil.png").read_bytes(), "image/png")
        self._send(404, b"not found", "text/plain")


if __name__ == "__main__":
    threading.Thread(target=refresh_loop, daemon=True).start()
    print(f"Tableau de bord : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
