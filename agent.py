"""Agent de clipping automatique.

  python agent.py                  # découvre les vidéos du moment, découpe et publie
  python agent.py --url <URL>      # traite une vidéo précise
  python agent.py --no-publish     # génère les clips sans publier
  python agent.py --channel en_business   # une autre chaîne (dossier channels/<id>/)
"""
import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import channels
import discover
import highlights
import media
import person
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
    video = media.download(src["url"], work, cfg["language"], cfg.get("creator", ""))
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
    # Twitch : l'« uploader » d'un clip est la personne qui l'a créé, pas le streamer
    creator = cfg.get("creator") or src.get("creator") or \
        (src.get("source") if src.get("platform") == "twitch" else None) or \
        info.get("uploader") or src.get("source", "").lstrip("@")
    who = person.from_cfg(cfg)
    keep = None
    if who:
        # campagne qui exige que la personne soit à l'image : on écarte les moments où elle
        # n'apparaît pas assez (ex. l'animatrice seule à l'écran)
        need = (cfg.get("person") or {}).get("min_visible", 0.6)

        def keep(c):
            vis = who.visibility(video, c["start"], c["end"])
            print(f"  {c['start']:.0f}s→{c['end']:.0f}s : {cfg.get('creator', 'personne')} "
                  f"visible {vis:.0%}{'' if vis >= need else ' → écarté'}")
            return vis >= need
    pick_cfg = cfg
    import discover as _disc
    if _disc.gaming_ok({**src, "creator": creator, "title": title}, cfg.get("watch") or {}):
        # Squeezie : le gaming est autorisé (ses réactions en jeu, seul ou avec d'autres)
        pick_cfg = {**cfg, "focus": (cfg.get("focus") or "") + "\n" + (cfg.get("watch") or {}).get(
            "gaming_ok_focus", "")}
    clips = highlights.pick(sents, pick_cfg, title, duration, creator, keep, video)[: cfg["clips_per_video"]]

    pub = cfg.get("publish") or {}
    publishing = do_publish

    for i, clip in enumerate(clips):
        out = cfg["_output"] / slug(sid) / f"clip_{i + 1:02d}.mp4"
        print(f"  [{i + 1}/{len(clips)}] {clip['start']:.0f}s→{clip['end']:.0f}s "
              f"score {clip.get('score')} | {clip.get('hook')}")
        clip["variant"] = len(state["clips"]) % 3
        clip["credit"] = creator
        render.render(video, words, clip, cfg["style"], out, lang=cfg.get("language", "fr"),
                      person=who)
        meta = {"file": str(out.relative_to(ROOT)), "source": src["url"], **clip,
                "made": time.time()}
        if src.get("source_ts"):   # date de mise en ligne de la vidéo source (fraîcheur)
            meta["source_ts"] = src["source_ts"]
        out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        if publishing:
            when = publish.next_slots(pub, state["taken_slots"], 1)[0]
            meta["publish"] = publish.publish(out, meta, pub, when, state)
            if meta["publish"].pop("sent_now", False):  # au moins une plateforme sur ce créneau
                state["taken_slots"].append(when.isoformat())
        state["clips"].append(meta)
        save_state(state)
    state["done_sources"].append(sid)
    save_state(state)
    if who:
        who.release()   # la reconnaissance faciale gardait la vidéo ouverte
    if work in video.parents or cfg.get("delete_sources_after"):
        # la vidéo source n'est plus utile (transcription et clips gardés) ; un fichier fourni
        # par une campagne n'est supprimé que si la chaîne le demande (delete_sources_after)
        try:
            video.unlink(missing_ok=True)
            if work not in video.parents:
                print(f"  source supprimée : {video.name}")
        except OSError as e:
            print(f"  (source pas encore supprimée : {e})")
    # sources déjà découpées lors d'un passage précédent mais restées sur le disque
    if cfg.get("delete_sources_after") and cfg.get("sources_dir"):
        d = Path(cfg["sources_dir"])
        d = d if d.is_absolute() else ROOT / d
        for p in d.glob("*.mp4"):
            if slug(p.stem) in state["done_sources"] and p != video:
                try:
                    p.unlink()
                    print(f"  source supprimée : {p.name}")
                except OSError:
                    pass


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
    ap.add_argument("--submit-only", action="store_true",
                    help="soumet seulement les posts en ligne à clipping.net (passage horaire)")
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
        if pub.get("buffer_channels"):
            print("  Chaîne YouTube pas encore connectée (youtube_auth.py) : publication Buffer seulement.")
            pub["backend"] = "buffer"
        elif not a.no_publish and not a.url:
            # passage automatique : inutile de monter des clips qui ne partiront jamais
            print("  Chaîne YouTube pas encore connectée (python youtube_auth.py --channel "
                  f"{a.channel}) : rien à faire.")
            return
        else:
            print("  youtube_channel pas encore renseigné : clips générés SANS publication.")
            a.no_publish = True
    if pub.get("backend") == "buffer" and not pub.get("buffer_channels"):
        print("  Buffer pas encore relié (publish.buffer_channels vide) : clips générés SANS publication.")
        a.no_publish = True

    if a.submit_only:
        # passage horaire : la file Buffer (10 posts max sur le plan Free) est rechargée au fil
        # de l'eau, sinon 15 posts/jour ne tiennent pas ; YouTube reste aux passages 6h / 15h
        if pub.get("buffer_channels") and not a.no_publish:
            publish.drop_stale(pub, state, save_state)
            publish.upload_pending(pub, state, save_state, youtube=False)
        if pub.get("clipping"):
            import clipping
            n = clipping.submit_live(cfg, state, save_state)
            print(f"[clipping.net] {n} post(s) soumis à la campagne")
        if not a.no_publish:
            publish.purge_published(pub, state, save_state)
        return

    # 0) stratégie fraîcheur : un clip trop vieux en file d'attente ne sortira plus
    if not a.no_publish:
        publish.drop_stale(pub, state, save_state)

    # 1) d'abord vider la file d'attente (clips déjà montés, bloqués par le quota)
    if not a.no_publish:
        publish.upload_pending(pub, state, save_state)
        # posts déjà en ligne → soumis à la campagne clipping.net (si la chaîne en a une)
        if pub.get("clipping"):
            import clipping
            n = clipping.submit_live(cfg, state, save_state)
            if n:
                print(f"[clipping.net] {n} post(s) soumis à la campagne")
        publish.purge_published(pub, state, save_state)   # déjà en ligne partout : fichiers supprimés

    # 2) ne pas produire plus que nécessaire : juste de quoi remplir ~`backlog_days` jour(s)
    #    de créneaux (les vidéos de demain seront clippées demain, quand elles seront fraîches)
    pending = sum(1 for c in state["clips"]
                  if (c.get("publish") or {}).get("pending") or (c.get("publish") or {}).get("yt_pending"))
    target = min(math.ceil(publish.per_day_capacity(pub) * pub.get("backlog_days", 1)),
                 pub.get("max_backlog", 150))
    need_clips = target - pending
    if a.no_publish:
        need_clips = max(need_clips, cfg["clips_per_video"])
    if not a.url and need_clips <= 0:
        print(f"{pending} clips déjà en attente (objectif {target}) : pas de nouvelle production.")
        save_state(state)
        return
    w = cfg.get("watch") or {}
    cap = w.get("videos_per_run", 2) if (w.get("youtube") or w.get("twitch") or w.get("search"))         else cfg.get("videos_per_run", 2)
    want = max(1, min(cap, math.ceil(need_clips / max(cfg["clips_per_video"], 1))))
    print(f"[production] {pending} en attente, objectif {target} → {need_clips} clip(s) à faire, "
          f"{want} vidéo(s) source max")

    def src_id(u):
        p = Path(u)
        return slug(p.stem if p.is_file() else u.split("=")[-1].split("/")[-1])

    if cfg.get("dropbox") and not a.url:   # streams de la campagne : on récupère les nouveaux
        import dropbox_sync
        try:
            dropbox_sync.sync(cfg, state)
        except Exception as e:
            print(f"  ✗ Dropbox : {str(e)[:200]}")

    if a.url:
        todo = [{"url": a.url, "id": src_id(a.url)}]
    else:
        todo = discover.find_trending(cfg, set(state["done_sources"]), state, want)             if (w.get("youtube") or w.get("twitch") or w.get("search")) else []
        save_state(state)   # relevé des vues (vitesse actuelle au prochain passage)
        # dossier de vidéos fournies par une campagne : chaque fichier non encore traité
        if cfg.get("sources_dir"):
            d = Path(cfg["sources_dir"])
            d = d if d.is_absolute() else ROOT / d
            files = [p for p in d.glob("*") if p.suffix.lower() in (".mp4", ".mkv", ".mov", ".webm")]
            # le stream le plus RÉCENT d'abord (date dans le nom « 2026-09-22-… », sinon date du fichier)
            files.sort(key=lambda p: (re.match(r"\d{4}-\d{2}-\d{2}", p.name).group(0)
                                      if re.match(r"\d{4}-\d{2}-\d{2}", p.name)
                                      else time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))),
                       reverse=True)
            todo += [{"url": str(p), "id": src_id(str(p)), "title": p.stem} for p in files
                     if src_id(str(p)) not in state["done_sources"]][:want]
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
    if not a.no_publish:   # clips montés aujourd'hui et déjà partout en ligne
        publish.purge_published(pub, state, save_state)
    if cfg.get("dropbox") and not a.url:   # streams prêts pour le prochain passage
        import dropbox_sync
        try:
            dropbox_sync.sync(cfg, state)
        except Exception as e:
            print(f"  ✗ Dropbox : {str(e)[:200]}")
    print("\nTerminé. Clips dans clipper/output/")


if __name__ == "__main__":
    main()
