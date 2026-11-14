"""Repère les belles ACTIONS d'une vidéo de jeu par le son, pas par les paroles.

Une belle action (but, arrêt fou, retournement) se voit à l'image, mais elle s'ENTEND aussi : cris du joueur,
du commentateur ou des potes en vocal, explosion de but, foule. On mesure le volume toutes les 0,5 s et on le
compare au niveau habituel de la minute autour : les pics nets et assez longs sont des moments forts.

  peaks(video)  →  [(t_secondes, intensité_dB), ...] du plus fort au moins fort
"""
import subprocess

import numpy as np

SR = 8000          # le son en 8 kHz mono suffit pour mesurer un volume
WIN = 0.5          # une mesure toutes les 0,5 s
BASE = 60.0        # niveau habituel = médiane sur 60 s autour


def loudness(video):
    """Volume (dB) toutes les WIN secondes."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True, timeout=1800)
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
    n = int(SR * WIN)
    if len(x) < n * 20:
        return np.zeros(0, dtype=np.float32)
    x = x[: len(x) // n * n].reshape(-1, n)
    rms = np.sqrt((x ** 2).mean(axis=1)) + 1.0
    return 20 * np.log10(rms)


def peaks(video, max_peaks=12, min_gap=25.0, min_db=7.0):
    """Pics de volume nettement au-dessus du niveau habituel, tenus au moins ~1 s, espacés de min_gap s."""
    db = loudness(video)
    if not len(db):
        return []
    half = int(BASE / WIN / 2)
    base = np.array([np.median(db[max(0, i - half): i + half + 1]) for i in range(len(db))])
    exc = db - base
    # lissage sur 1,5 s : un bruit d'une fraction de seconde (clic, micro) n'est pas une action
    k = int(1.5 / WIN)
    smooth = np.convolve(exc, np.ones(k) / k, mode="same")
    floor = max(min_db, float(np.percentile(smooth, 97)))
    order = np.argsort(smooth)[::-1]
    out = []
    for i in order:
        if smooth[i] < floor or len(out) >= max_peaks:
            break
        t = i * WIN
        if all(abs(t - u) >= min_gap for u, _ in out):
            out.append((round(t, 1), round(float(smooth[i]), 1)))
    return out


def frames(video, times, width=512):
    """Images JPEG (base64) aux instants donnés, pour qu'une IA qui voit juge l'action."""
    import base64
    out = []
    for t in times:
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "-"],
                           capture_output=True, timeout=60)
        if p.stdout:
            out.append(base64.b64encode(p.stdout).decode())
    return out


def wanted(cfg):
    """Chasse aux actions : demandée dans la config (action_moments), ou niche/consignes de jeu vidéo."""
    if "action_moments" in cfg:
        return bool(cfg["action_moments"])
    text = f"{cfg.get('niche', '')} {cfg.get('focus', '')}".lower()
    return any(w in text for w in ("gaming", "gameplay", "esport", "e-sport", "jeu vidéo", "jeux vidéo",
                                   "rocket league", "fortnite", "valorant", "fifa", "league of legends",
                                   "call of duty", "counter-strike", "speedrun", "game"))
