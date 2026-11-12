"""Agent de clipping automatique.

  python agent.py                  # découvre les vidéos du moment, découpe et publie
  python agent.py --url <URL>      # traite une vidéo précise
  python agent.py --no-publish     # génère les clips sans publier
  python agent.py --channel en_business   # une autre chaîne (dossier channels/<id>/)
"""
import argparse
import json
import re
import sys
from pathlib import Path

import channels
import discover
import highlights
import media
import publish
import render

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
sys.stdout.reconfigure(encoding="utf-8")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"done_sources": [], "clips": [], "taken_slots": []}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:60]


def process(src, cfg, state, do_publish):
    sid = src["id"]
    work = ROOT / "work" / slug(sid)  # cache partagé entre chaînes (clé = id de la vidéo)
    print(f"\n=== {src.get('title') or src['url']}")
    print("  téléchargement…")
    video = media.download(src["url"], work, cfg["language"])
    info = json.loads((work / "info.json").read_text(encoding="utf-8")) \
        if (work / "info.json").exists() else {}
    title = src.get("title") or info.get("title", "")
    duration = src.get("duration") or info.get("duration") or render_duration(video)

    print("  transcription…")
    words = media.transcribe(video, cfg["language"], cfg["whisper_model"], src["url"])
    if not words:
        print("  aucune parole détectée, vidéo ignorée")
        state["done_sources"].append(sid)
        return
    sents = media.sentences(words)

    print("  sélection des meilleurs moments…")
    clips = highlights.pick(sents, cfg, title, duration)[: cfg["clips_per_video"]]

    pub = cfg.get("publish") or {}
    publishing = do_publish

    for i, clip in enumerate(clips):
        out = cfg["_output"] / slug(sid) / f"clip_{i + 1:02d}.mp4"
        print(f"  [{i + 1}/{len(clips)}] {clip['start']:.0f}s→{clip['end']:.0f}s "
              f"score {clip.get('score')} | {clip.get('hook')}")
        clip["variant"] = len(state["clips"]) % 3
        clip["credit"] = info.get("uploader") or src.get("source", "").lstrip("@")
        render.render(video, words, clip, cfg["style"], out, lang=cfg.get("language", "fr"))
        meta = {"file": str(out.relative_to(ROOT)), "source": src["url"], **clip}
        out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        if publishing:
            when = publish.next_slots(pub, state["taken_slots"], 1)[0]
            meta["publish"] = publish.publish(out, meta, pub, when, state)
            if not meta["publish"].get("pending"):
                state["taken_slots"].append(when.isoformat())
        state["clips"].append(meta)
        save_state(state)
    state["done_sources"].append(sid)
    save_state(state)
    video.unlink(missing_ok=True)  # la vidéo source n'est plus utile (transcription et clips gardés)


def render_duration(video):
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="vidéo précise à clipper")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--channel", help=f"chaîne secondaire : {', '.join(channels.available())}")
    a = ap.parse_args()

    global STATE
    cfg = channels.load(a.channel, a.config)
    STATE = cfg["_state"]
    publish.configure(cfg)
    state = load_state()
    pub = cfg.get("publish") or {}
    print(f"### Chaîne : {pub.get('youtube_channel') or a.channel or 'principale'} "
          f"({cfg.get('language')}) — {cfg.get('niche')}")
    if pub.get("backend") == "youtube_api" and "REMPLACE" in str(pub.get("youtube_channel")):
        print("  youtube_channel pas encore renseigné : clips générés SANS publication.")
        a.no_publish = True

    # 1) d'abord vider la file d'attente (clips déjà montés, bloqués par le quota)
    if not a.no_publish:
        publish.upload_pending(pub, state, save_state)

    # 2) ne pas produire plus que nécessaire : on garde au max `max_backlog` clips en attente
    pending = sum(1 for c in state["clips"] if (c.get("publish") or {}).get("pending"))
    if not a.url and pending >= pub.get("max_backlog", 150):
        print(f"{pending} clips déjà en attente : pas de nouvelle production aujourd'hui.")
        return

    if a.url:
        todo = [{"url": a.url, "id": slug(a.url.split("=")[-1].split("/")[-1])}]
    else:
        todo = discover.find_trending(cfg, set(state["done_sources"]))
        todo += [{"url": u, "id": slug(u.split("=")[-1].split("/")[-1])}
                 for u in cfg.get("sources") or []
                 if slug(u.split("=")[-1].split("/")[-1]) not in state["done_sources"]]
    if not todo:
        print("Rien de nouveau à clipper. Ajoute des chaînes dans config.yaml (watch:).")
        return
    for src in todo:
        try:
            process(src, cfg, state, not a.no_publish)
        except Exception as e:
            print(f"  ERREUR sur {src['url']} : {e}")
    print("\nTerminé. Clips dans clipper/output/")


if __name__ == "__main__":
    main()
