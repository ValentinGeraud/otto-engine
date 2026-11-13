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
from zoneinfo import ZoneInfo

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


# ---------- Calendrier des sorties ----------
def calendar(ch="", days_back=10, days_ahead=21):
    """Toutes les sorties : publiées, programmées sur YouTube, et la file d'attente projetée
    sur les prochains créneaux (en tenant compte des clips épinglés et des jours sans sortie)."""
    from datetime import timedelta
    cfg, state, an = _chan(ch)
    pub = cfg.get("publish") or {}
    tz = ZoneInfo(pub.get("timezone", "Europe/Paris"))
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(days=days_back), now + timedelta(days=days_ahead)
    pins = pub.get("_pins") or {}
    ev, pending = [], []
    for c in state.get("clips", []):
        p = c.get("publish") or {}
        base = {"key": c.get("file"), "hook": c.get("hook") or c.get("title"), "score": c.get("score")}
        if p.get("youtube_id") and p.get("when"):
            w = datetime.fromisoformat(p["when"])
            if lo <= w <= hi:
                vid = p["youtube_id"]
                ev.append(base | {"when": w.isoformat(), "status": "publié" if w <= now else "programmé",
                                  "locked": True, "url": f"https://youtu.be/{vid}", "id": vid,
                                  "views": an["per_video"].get(vid, {}).get("views")})
        elif p.get("pending"):
            pending.append(base)
    # projection de la file : d'abord les épinglés, puis les créneaux libres dans l'ordre
    soon = datetime.now(tz) + timedelta(minutes=30)

    def pinned_at(c):
        w = pins.get(c["key"])
        w = datetime.fromisoformat(w) if w else None
        return w if w and w > soon else None

    rest = [c for c in pending if not pinned_at(c)]
    free = []
    if pub and rest:
        try:
            free = publish.next_slots(pub, list(state.get("taken_slots", [])), min(len(rest), 200))
        except Exception:
            free = []
    for c in pending:
        w = pinned_at(c)
        pinned = bool(w)
        if not w:
            if not free:
                continue
            w = free.pop(0)
        if w <= hi:
            ev.append(c | {"when": w.isoformat(), "status": "prévu", "locked": False, "pinned": pinned})
    ev.sort(key=lambda e: e["when"])
    return ev


def schedule_info(ch=""):
    cfg, state, _ = _chan(ch)
    pub = cfg.get("publish") or {}
    if not pub:
        return {}
    return {"timezone": pub.get("timezone", "Europe/Paris"), "window": pub.get("window"),
            "slots_today": publish.day_slots(pub), "custom": bool(pub.get("_custom_slots")),
            "slots": pub.get("slots") if pub.get("_custom_slots") else None,
            "ramp_today": publish.ramp_today(pub), "per_day": pub.get("per_day"),
            "api_per_day": pub.get("api_uploads_per_day", 6),
            "off_days": pub.get("_off_days") or [], "pins": len(pub.get("_pins") or {})}


# ---------- Analytics détaillées ----------
def _hour_floor(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def analytics_detail(ch=""):
    """Courbe horaire des vues cumulées, tableau de chaque Short, records."""
    from datetime import timedelta
    cfg, state, an = _chan(ch)
    raw = analytics.load(cfg["_analytics"])
    vids = raw.get("videos", {})
    tz = ZoneInfo((cfg.get("publish") or {}).get("timezone", "Europe/Paris"))
    meta = {}
    for c in state.get("clips", []):
        p = c.get("publish") or {}
        if p.get("youtube_id"):
            meta[p["youtube_id"]] = {"hook": c.get("hook") or c.get("title"), "when": p.get("when"),
                                     "file": c.get("file"), "score": c.get("score")}
    hist = {}
    for vid, v in vids.items():
        pts = []
        for t, n in v.get("history", []):
            try:
                pts.append((datetime.fromisoformat(t), int(n)))
            except Exception:
                pass
        if pts:
            hist[vid] = sorted(pts)
    now = datetime.now()
    series = []
    if hist:
        start = _hour_floor(min(p[0][0] for p in hist.values()))
        start = max(start, _hour_floor(now) - timedelta(days=14))
        h, idx, last = start, {k: 0 for k in hist}, {k: 0 for k in hist}
        while h <= _hour_floor(now):
            end = h + timedelta(hours=1)
            for k, pts in hist.items():
                while idx[k] < len(pts) and pts[idx[k]][0] < end:
                    last[k] = pts[idx[k]][1]
                    idx[k] += 1
            series.append([h.strftime("%Y-%m-%dT%H:00"), sum(last.values())])
            h = end
    table = []
    for vid, v in vids.items():
        m = meta.get(vid, {})
        pts = hist.get(vid, [])
        recent = [p for p in pts if p[0] >= now - timedelta(hours=3)]
        vph = 0
        if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
            vph = (recent[-1][1] - recent[0][1]) / ((recent[-1][0] - recent[0][0]).total_seconds() / 3600)
        pub_at = None
        if m.get("when"):
            try:
                pub_at = datetime.fromisoformat(m["when"])
            except Exception:
                pass
        age_h = (datetime.now(timezone.utc) - pub_at).total_seconds() / 3600 if pub_at else None
        first24 = None
        if pub_at and pts and age_h and age_h >= 24:
            lim = pub_at.astimezone().replace(tzinfo=None) + timedelta(hours=24)
            within = [p[1] for p in pts if p[0] <= lim]
            first24 = within[-1] if within else None
        spark, seen = [], set()   # une valeur par heure
        for t, n in pts:
            key = t.strftime("%m%d%H")
            if key not in seen:
                seen.add(key)
                spark.append(n)
        views = v.get("views", 0)
        table.append({"id": vid, "hook": m.get("hook") or vid, "when": m.get("when"),
                      "file": m.get("file"), "score": m.get("score"), "views": views,
                      "likes": v.get("likes", 0), "comments": v.get("comments", 0),
                      "privacy": v.get("privacy"), "vph": round(vph, 1),
                      "age_h": round(age_h, 1) if age_h is not None else None,
                      "first24": first24, "spark": spark[-48:],
                      "like_rate": round(v.get("likes", 0) / views * 100, 2) if views else 0})
    table.sort(key=lambda r: r["views"], reverse=True)
    live = [r for r in table if r["age_h"] is None or r["age_h"] >= 0]
    views = sorted(r["views"] for r in live)
    by_hour = {}
    for r in live:
        if r["when"] and r["age_h"] and r["age_h"] >= 12:  # assez de recul pour comparer
            hh = datetime.fromisoformat(r["when"]).astimezone(tz).hour
            by_hour.setdefault(hh, []).append(r["first24"] or r["views"])
    return {
        "series": series,
        "table": table[:80],
        "kpi": {
            "shorts": len(live),
            "avg": round(sum(views) / len(views)) if views else 0,
            "median": views[len(views) // 2] if views else 0,
            "best": table[0]["views"] if table else 0,
            "last_hour": series[-1][1] - series[-2][1] if len(series) >= 2 else 0,
            "like_rate": round(sum(r["likes"] for r in live) / max(sum(views), 1) * 100, 2),
            "comment_rate": round(sum(r["comments"] for r in live) / max(sum(views), 1) * 100, 3),
            "over_1k": sum(v >= 1000 for v in views),
        },
        "by_hour": {str(k): round(sum(v) / len(v)) for k, v in sorted(by_hour.items())},
        "updated": raw.get("updated"),
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
