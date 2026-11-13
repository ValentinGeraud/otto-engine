"""Bruitages drôles posés automatiquement au montage (chaîne fr).

Sons utilisés, et où :
  whoosh  apparition du hook (0 s) et passage au verdict
  boom    « vine boom » grave sur certains plans serrés (punch-in), avec flash blanc
  boing   sur les rires détectés dans la transcription ([rires])
  pop     sur les mots-clés colorés (chiffres, argent, police...)
  ding    quand la question au spectateur apparaît

Les sons sont générés ici (synthèse, aucun droit d'auteur). Pour mettre tes propres sons
tendance, dépose un fichier du même nom dans branding/sfx/ (ex. boom.mp3, boing.wav) :
il remplace automatiquement le son généré. Prends-les dans une banque libre de droits
(YouTube Audio Library, Pixabay Sound Effects...) pour éviter les réclamations.
"""
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
SR = 48000
USER_DIR = ROOT / "branding" / "sfx"
NAMES = ("whoosh", "boom", "boing", "pop", "ding", "swish", "kaching", "scratch", "riser",
         "glitch", "fail", "bass", "tick", "hit", "reverse")
GAIN = {"whoosh": 0.55, "boom": 0.9, "boing": 0.6, "pop": 0.45, "ding": 0.5, "swish": 0.35,
        "kaching": 0.55, "scratch": 0.6, "riser": 0.45, "glitch": 0.35, "fail": 0.55, "bass": 0.8,
        "tick": 0.4, "hit": 0.85, "reverse": 0.45}


# ---------- synthèse ----------
def _t(d):
    return np.arange(int(SR * d)) / SR


def _sweep(f0, f1, d, curve="exp"):
    """Sinus dont la fréquence glisse de f0 à f1 (phase continue)."""
    t = _t(d)
    f = f0 * (f1 / f0) ** (t / d) if curve == "exp" else f0 + (f1 - f0) * t / d
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _whoosh():
    d = 0.45
    n = len(_t(d))
    rng = np.random.default_rng(7)
    x = rng.standard_normal(n)
    # passe-bande dont la fréquence monte puis redescend (filtre d'état variable)
    fc = 300 + 2700 * np.sin(np.pi * np.linspace(0, 1, n)) ** 2
    lp = bp = 0.0
    out = np.empty(n)
    q = 0.6
    for i in range(n):
        f = 2 * np.sin(np.pi * fc[i] / SR)
        hp = x[i] - lp - q * bp
        bp += f * hp
        lp += f * bp
        out[i] = bp
    env = np.sin(np.pi * np.linspace(0, 1, n)) ** 1.5
    return out * env


def _boom():
    d = 1.1
    t = _t(d)
    body = _sweep(110, 38, d) * np.exp(-t * 3.2)
    sub = _sweep(55, 30, d) * np.exp(-t * 2.5) * 0.6
    click = np.random.default_rng(3).standard_normal(len(t)) * np.exp(-t * 90) * 0.5
    x = np.tanh((body + sub + click) * 2.6)  # saturation : le côté « mème »
    return x * np.minimum(t / 0.004, 1)


def _boing():
    d = 0.55
    t = _t(d)
    f = 180 + 260 * t / d + 70 * np.sin(2 * np.pi * 16 * t) * np.exp(-t * 3)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)
    return x + 0.25 * np.sin(2 * np.pi * np.cumsum(2 * f) / SR) * np.exp(-t * 6)


def _pop():
    d = 0.09
    t = _t(d)
    return _sweep(420, 1500, d) * np.exp(-t * 45) * np.minimum(t / 0.002, 1)


def _ding():
    d = 1.0
    t = _t(d)
    x = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * k)
            for f, a, k in ((1318.5, 1.0, 4.0), (2637, 0.35, 6.0), (3956, 0.15, 9.0)))
    return x * np.minimum(t / 0.003, 1)


def _swish():
    """Petit whoosh court et aigu (changement de plan)."""
    d = 0.22
    t = _t(d)
    rng = np.random.default_rng(11)
    x = np.diff(rng.standard_normal(len(t) + 1))  # bruit « clair »
    env = np.sin(np.pi * np.minimum(t / d, 1)) ** 2
    return x * env * 0.6


def _kaching():
    """Caisse enregistreuse : clic métallique puis cloches."""
    d = 0.9
    t = _t(d)
    click = np.random.default_rng(5).standard_normal(len(t)) * np.exp(-t * 120)
    bell = sum(a * np.sin(2 * np.pi * f * np.maximum(t - 0.08, 0)) * np.exp(-np.maximum(t - 0.08, 0) * k)
               for f, a, k in ((2093, 1.0, 5), (2637, 0.8, 6), (3136, 0.6, 7), (4186, 0.3, 9)))
    bell = bell * (t > 0.08)
    return click * 0.6 + bell * 0.7


def _scratch():
    """Scratch de disque (« record scratch ») : arrêt net."""
    d = 0.45
    t = _t(d)
    rng = np.random.default_rng(9)
    noise = rng.standard_normal(len(t))
    f = 900 * (1 + 0.8 * np.sin(2 * np.pi * 7 * t))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR)
    x = (noise * 0.5 + tone * 0.6) * np.abs(np.sin(2 * np.pi * 7 * t))
    return np.tanh(x * 2) * np.exp(-t * 3)


def _riser():
    """Montée de tension avant la carte de fin."""
    d = 1.2
    t = _t(d)
    rng = np.random.default_rng(13)
    tone = _sweep(200, 1600, d) * 0.5 + _sweep(300, 2400, d) * 0.3
    x = (tone + rng.standard_normal(len(t)) * 0.25) * (t / d) ** 2
    return x * np.minimum((d - t) / 0.02, 1)


def _glitch():
    """Bip numérique haché (apparition du hook)."""
    d = 0.3
    t = _t(d)
    gate = (np.floor(t * 40) % 2 == 0).astype(float)
    x = np.sign(np.sin(2 * np.pi * (600 + 900 * (np.floor(t * 40) % 3)) * t)) * gate
    return x * 0.5 * np.exp(-t * 4)


def _fail():
    """Trombone triste stylisé (trois notes qui descendent)."""
    notes, out = (392, 370, 349, 330), []
    for i, f in enumerate(notes):
        d = 0.22 if i < 3 else 0.6
        t = _t(d)
        vib = 1 + (0.02 * np.sin(2 * np.pi * 6 * t) if i == 3 else 0)
        ph = 2 * np.pi * np.cumsum(f * vib) / SR
        x = sum(np.sin(k * ph) / k for k in range(1, 6))
        out.append(x * np.minimum(t / 0.02, 1) * np.minimum((d - t) / 0.03, 1))
    return np.concatenate(out)


def _bass():
    """Grosse basse (drop) : sur les moments les plus forts."""
    d = 0.8
    t = _t(d)
    x = _sweep(80, 32, d) * np.exp(-t * 2.5)
    return np.tanh(x * 3) * np.minimum(t / 0.003, 1)


def _hit():
    """Impact cinéma : coup sourd grave + souffle bref (pas de saturation « mème »)."""
    d = 0.9
    t = _t(d)
    low = _sweep(95, 38, d) * np.exp(-t * 4.5)
    rng = np.random.default_rng(21)
    air = rng.standard_normal(len(t)) * np.exp(-t * 28) * 0.35
    body = np.sin(2 * np.pi * 180 * t) * np.exp(-t * 18) * 0.3
    return np.tanh((low + air + body) * 1.4) * np.minimum(t / 0.002, 1)


def _reverse():
    """Aspiration (cymbale inversée) qui monte et coupe net : annonce un moment fort."""
    d = 0.7
    t = _t(d)
    rng = np.random.default_rng(17)
    x = rng.standard_normal(len(t))
    lp = np.convolve(x, np.ones(6) / 6, mode="same")  # adoucit le bruit
    tone = _sweep(400, 1800, d) * 0.25
    return (lp * 0.8 + tone) * (t / d) ** 3


def _tick():
    d = 0.04
    t = _t(d)
    return np.sin(2 * np.pi * 2500 * t) * np.exp(-t * 150)


SYNTH = {"whoosh": _whoosh, "boom": _boom, "boing": _boing, "pop": _pop, "ding": _ding,
         "swish": _swish, "kaching": _kaching, "scratch": _scratch, "riser": _riser,
         "glitch": _glitch, "fail": _fail, "bass": _bass, "tick": _tick,
         "hit": _hit, "reverse": _reverse}


# ---------- chargement ----------
_cache = {}


def _decode(path: Path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def sound(name):
    """Son mono normalisé (pic à 1). Fichier de branding/sfx/ s'il existe, sinon synthèse."""
    if name in _cache:
        return _cache[name]
    user = sorted(USER_DIR.glob(f"{name}.*")) if USER_DIR.exists() else []
    x = None
    for f in user:
        try:
            x = _decode(f)
            break
        except Exception:
            continue
    if x is None or not len(x):
        x = SYNTH[name]()
    x = x / (np.abs(x).max() or 1)
    _cache[name] = x
    return x


# ---------- placement ----------
def plan(out_words, segs, body, total, ed, hook=True, question_at=None, keyword_re=None):
    """Liste [(temps, son)] en temps de sortie. Peu de sons, bien placés : trop = agaçant."""
    gap = ed.get("sfx_min_gap", 0.8)
    ev = []

    def add(t, name, spacing=0.0):
        if t < 0 or t > total - 0.05:
            return False
        if any(abs(t - u) < gap for u, _ in ev):
            return False
        if spacing and any(n == name and abs(t - u) < spacing for u, n in ev):
            return False
        ev.append((t, name))
        return True

    if hook:
        add(0.0, "whoosh")
    if question_at is not None:
        add(question_at, "ding")
    if body < total:
        add(max(body - 0.2, 0), "whoosh")

    # boom : sur les plans serrés (punch-in), max 3 par clip, au moins 6 s d'écart
    off, booms = 0.0, 0
    for a, b, z in segs:
        if z > 1 and off > 1.5 and booms < ed.get("sfx_max_boom", 3):
            if add(off, "boom", spacing=6.0):
                booms += 1
        off += b - a

    # boing : sur les rires de la transcription
    laughs = 0
    for w in out_words:
        if "rire" in w["w"].lower() or "laugh" in w["w"].lower() or "risa" in w["w"].lower():
            if laughs < 3 and add(w["s"], "boing", spacing=4.0):
                laughs += 1

    # pop : mots-clés colorés
    pops = 0
    if keyword_re is not None:
        for w in out_words:
            if w["s"] < body and keyword_re.search(w["w"]) and not w["w"].startswith("["):
                if pops < 4 and add(w["s"], "pop", spacing=2.5):
                    pops += 1
    return sorted(ev)


def build_track(events, total, path: Path, volume=0.5):
    """Piste stéréo 48 kHz (WAV) de la durée du Short, avec tous les bruitages."""
    n = int(SR * (total + 0.1))
    mix = np.zeros(n)
    for t, name in events:
        x = sound(name) * GAIN.get(name, 0.5)
        i = int(t * SR)
        j = min(i + len(x), n)
        if i < n:
            mix[i:j] += x[: j - i]
    mix *= volume
    peak = np.abs(mix).max()
    if peak > 0.98:
        mix *= 0.98 / peak
    pcm = (np.repeat(mix[:, None], 2, axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())
    return path


def export_bank(folder: Path = ROOT / "branding" / "sfx" / "_apercu"):
    """Écrit chaque son généré en WAV pour les écouter."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        build_track([(0.05, name)], len(SYNTH[name]()) / SR + 0.1, folder / f"{name}.wav", 1.0)
    return folder


if __name__ == "__main__":
    print(f"Aperçus écrits dans {export_bank()}")
