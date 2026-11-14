"""Téléchargement (yt-dlp) et transcription mot à mot (faster-whisper)."""
import json
import os
import re
from pathlib import Path

import yt_dlp


def download(url, workdir: Path, language="fr", uploader="") -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    local = Path(url)
    if local.is_file():  # fichier fourni par une campagne (Dropbox, Drive…) : rien à télécharger
        (workdir / "info.json").write_text(json.dumps(
            {"title": local.stem, "uploader": uploader, "webpage_url": str(local),
             "duration": None}, ensure_ascii=False), encoding="utf-8")
        return local
    done = [p for p in workdir.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm")
            and p.name.count(".") == 1 and (workdir / "info.json").exists()]
    if done:
        return done[0]
    for p in workdir.glob("source.*"):  # restes d'un téléchargement interrompu
        p.unlink(missing_ok=True)
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
        # sous-titres auto minutés mot à mot, récupérés dans la même requête que la vidéo
        "writeautomaticsub": True, "writesubtitles": True,
        "subtitleslangs": [language], "subtitlesformat": "json3",
        "ignoreerrors": "only_download", "retries": 5, "sleep_interval_subtitles": 2,
        "socket_timeout": 30,
    }
    # Dans le cloud, YouTube exige une session (« Sign in to confirm you're not a bot ») :
    # cookies d'un compte YouTube secondaire, au format Netscape (YTDLP_COOKIES = chemin du fichier).
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies and Path(cookies).is_file():
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    has_video = any(p.name.count(".") == 1 and p.suffix in (".mp4", ".mkv", ".webm")
                    for p in workdir.glob("source.*"))
    if not info or not has_video:
        # YouTube limite souvent les sous-titres (HTTP 429) et yt-dlp abandonne toute la vidéo :
        # on recommence SANS sous-titres, Whisper transcrira à la place.
        print("  (sous-titres refusés par YouTube : téléchargement sans, Whisper prendra le relais)")
        for p in workdir.glob("source.*"):
            p.unlink(missing_ok=True)
        opts.update(writeautomaticsub=False, writesubtitles=False, ignoreerrors=False)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    (workdir / "info.json").write_text(json.dumps(
        {k: info.get(k) for k in ("title", "uploader", "webpage_url", "duration")},
        ensure_ascii=False), encoding="utf-8")
    return next(p for p in workdir.glob("source.*") if p.name.count(".") == 1)


def _parse_json3(data):
    words = []
    for ev in data.get("events", []):
        t0 = ev.get("tStartMs", 0)
        for sg in ev.get("segs") or []:
            txt = (sg.get("utf8") or "").strip()
            if txt and not re.fullmatch(r"\[[^\]]*\]", txt):  # [rires], [musique]…
                words.append({"w": txt, "s": round((t0 + sg.get("tOffsetMs", 0)) / 1000, 2)})
    if len(words) < 30:
        return None
    for i, w in enumerate(words):  # fin d'un mot = début du suivant (max 1 s)
        nxt = words[i + 1]["s"] if i + 1 < len(words) else w["s"] + 0.5
        w["e"] = round(min(nxt, w["s"] + 1.0), 2)
    return words


def youtube_words(url, language, workdir: Path = None):
    """Sous-titres automatiques YouTube, déjà minutés mot à mot (format json3).
    Quelques secondes au lieu d'une transcription Whisper d'une heure. None si indisponible."""
    if workdir:  # déjà téléchargés avec la vidéo ?
        for f in workdir.glob(f"source.{language}*.json3"):
            try:
                return _parse_json3(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    if "youtube.com" not in url and "youtu.be" not in url:
        return None
    import time
    import urllib.request
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                                   "socket_timeout": 20, "retries": 1}) as ydl:
                info = ydl.extract_info(url, download=False)
            subs = (info.get("automatic_captions") or {}).get(language) or                    (info.get("subtitles") or {}).get(language)
            fmt = next((s for s in subs or [] if s.get("ext") == "json3"), None)
            if not fmt:
                return None
            with urllib.request.urlopen(fmt["url"], timeout=30) as r:
                return _parse_json3(json.loads(r.read().decode("utf-8")))
        except Exception as e:
            print(f"  (sous-titres YouTube, essai {attempt + 1}/2 : {str(e)[:60]})")
            if "429" in str(e):  # YouTube nous limite : inutile d'insister, Whisper (GPU) prend le relais
                return None
            time.sleep(10)
    return None


_model = None


def _cuda_dlls():
    """Rend visibles les DLL CUDA installées par pip (nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    import glob
    import os
    import site
    for sp in site.getsitepackages():
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            if d not in os.environ["PATH"]:
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                try:
                    os.add_dll_directory(d)
                except (OSError, AttributeError):
                    pass


def transcribe(video: Path, language: str, model_size: str, url: str = ""):
    """Retourne une liste de mots [{w, s, e}] (texte, début, fin en secondes). Mis en cache."""
    # vidéo téléchargée : work/<id>/source.mp4 → work/<id>/transcript.json
    # fichier local (dossier de campagne) : un cache PAR vidéo, à côté d'elle
    cache = video.parent / "transcript.json" if video.stem == "source" \
        else video.with_name(video.stem + ".transcript.json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    words = youtube_words(url, language, video.parent) if url else None
    if words:
        print(f"  (sous-titres YouTube : {len(words)} mots, pas de transcription nécessaire)")
        cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        return words
    global _model
    _cuda_dlls()
    from faster_whisper import WhisperModel
    if _model is None:
        try:  # carte NVIDIA : 10 à 20 fois plus rapide que le processeur
            _model = WhisperModel(model_size, device="cuda", compute_type="float16")
        except Exception:
            _model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def run():
        segs, _ = _model.transcribe(str(video), language=language,
                                    word_timestamps=True, vad_filter=True)
        return [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)}
                for seg in segs for w in (seg.words or [])]
    try:
        words = run()
    except RuntimeError as e:  # GPU détecté mais CUDA incomplet : on repasse sur CPU
        if "cublas" not in str(e).lower() and "cuda" not in str(e).lower():
            raise
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        words = run()
    cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


def sentences(words, max_gap=0.8):
    """Regroupe les mots en phrases horodatées (pour le prompt de l'IA)."""
    out, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > max_gap or cur[-1]["w"].endswith((".", "?", "!"))):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return [{"s": c[0]["s"], "e": c[-1]["e"], "t": " ".join(x["w"] for x in c)} for c in out]
