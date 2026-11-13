"""Passe de montage Remotion (gratuit, local) posée sur le clip de base sorti par render.py.

render.py coupe, recadre, étalonne et mixe le son ; ici on prépare les « props » (mots, effets,
couleurs, éléments de campagne) puis on lance remotion/render.mjs, qui ajoute zooms, punch-in,
tremblements, flashs, emojis, sous-titres animés et bruitages.

Remotion est gratuit pour un particulier ou une structure de 3 personnes maximum.
"""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
RM_DIR = ROOT / "remotion"
SFX_DIR = ROOT / "branding" / "sfx" / "_remotion"

# mot prononcé → emoji qui surgit (fr / en / es)
EMOJI = [
    (r"\b(ha){2,}|\bmdr|\blol\b|\blmao|\bjaja|rire|laugh|risa", "😂"),
    (r"million|milliard|billion|money|argent|dinero|cash|dollars?|euros?|\$|€|riche|rich\b|rico|paid|payé", "💰"),
    (r"\bmort\b|\bdead\b|\bdeath|muerte|muerto|\bkill|tué", "💀"),
    (r"police|cops?\b|policía|arrest|prison|jail|cárcel", "🚨"),
    (r"\bfire\b|\bfeu\b|fuego|\bhot\b|chaud|caliente", "🔥"),
    (r"crazy|insane|fou\b|folle|dingue|loco|locura|wtf|what\?", "🤯"),
    (r"secret|secreto|chut", "🤫"),
    (r"\blove\b|amour|amor|\bcrush|kiss|bisou|beso", "😍"),
    (r"crash|broke|fauché|perdu|lost\b|perdí|fail", "📉"),
    (r"\bno+\b|jamais|never|nunca|\bnon\b", "❌"),
    (r"\bwow\b|incroyable|incredible|increíble|amazing", "😱"),
    (r"\bgta\b|\bgame\b|\bjeu\b|juego", "🎮"),
]
EMOJI = [(re.compile(p, re.I), e) for p, e in EMOJI]


def _emoji(word):
    for rx, e in EMOJI:
        if rx.search(word):
            return e
    return None


def _ass_rgb(c):
    """Couleur ASS (BBGGRR ou &HAABBGGRR) → #RRGGBB."""
    c = c.replace("&H", "").replace("&", "")[-6:].rjust(6, "0")
    return f"#{c[4:6]}{c[2:4]}{c[0:2]}"


def _ffmpeg_rgb(c):
    """Couleur ffmpeg 0xRRGGBB → #RRGGBB."""
    return "#" + c.replace("0x", "").replace("#", "")[:6]


def sfx_files():
    """Écrit chaque bruitage (généré ou fourni dans branding/sfx/) en WAV pour Remotion."""
    import sfx
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in sfx.NAMES:
        p = SFX_DIR / f"{name}.wav"
        user = sorted(sfx.USER_DIR.glob(f"{name}.*"))
        if not p.exists() or (user and user[0].stat().st_mtime > p.stat().st_mtime):
            sfx.build_track([(0.0, name)], len(sfx.sound(name)) / sfx.SR + 0.05, p, 1.0)
        out[name] = "local:" + str(p.resolve())
    return out


LAUGH = re.compile(r"(ha){2,}|mdr|lol|lmao|jaja|rire|laugh|risa", re.I)
MONEY = re.compile(r"million|milliard|billion|money|argent|dinero|cash|dollars?|euros?|\$|€|"
                   r"riche|rich|rico|paid|payé|salaire|salary", re.I)
FAIL = re.compile(r"broke|fauché|fail|raté|perdu|lost|perdí|ruin|faillite|bankrupt", re.I)
STOP = re.compile(r"^(wait|stop|attends|attendez|espera|non+|no+|never|jamais|nunca)[.,!?]*$", re.I)


def loud_peaks(base: Path, body, n=6, min_gap=4.0):
    """Moments où la voix monte (cri, emphase, éclat de rire) dans le clip de base : [(t, force)]."""
    import numpy as np
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(base), "-t", f"{body:.2f}", "-vn",
                              "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                             capture_output=True, timeout=120).stdout
    except Exception:
        return []
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    win = 1600  # 100 ms
    m = len(x) // win
    if m < 20:
        return []
    rms = 20 * np.log10(np.sqrt((x[:m * win].reshape(m, win) ** 2).mean(1)) + 1e-9)
    med = np.median(rms)
    # montée brutale par rapport à la seconde précédente + niveau au-dessus de la normale
    rise = rms - np.convolve(rms, np.ones(10) / 10, mode="full")[:m]
    score = (rms - med) + rise
    out = []
    for i in np.argsort(-score):
        t = i * 0.1
        if score[i] < 6 or t < 1.2 or t > body - 0.8:
            continue
        if all(abs(t - u) >= min_gap for u, _ in out):
            out.append((round(float(t), 2), float(score[i])))
        if len(out) >= n:
            break
    return out


def plan_fx(base: Path, words, segs, body, total, ed, hook=True, card_at=None, question_at=None,
            keyword_re=None, reserved=()):
    """Place bruitages + effets visuels (en temps de sortie). Les sons sont choisis selon ce qui se
    passe : pics de voix → boom/punch, rires → boing 😂, argent → ka-ching 💰, échec → trombone,
    « attends / jamais » → scratch, changement de plan → swish, carte de fin → riser + whoosh."""
    if ed.get("fx_style", "funny") != "funny":
        return plan_fx_tension(base, words, segs, body, total, ed, hook, card_at, question_at,
                               keyword_re, reserved)
    gap = ed.get("sfx_min_gap", 0.6)
    density = ed.get("sfx_density", 2.2)   # au plus 1 son toutes les ~2,2 s en moyenne
    budget = max(int(body / density), 3)
    fx = [{"t": t, "kind": "_reserve"} for t in reserved]  # sons de l'animation abonnement/like
    counts = {}

    def free(t, g=gap):
        return all(abs(t - f["t"]) >= g for f in fx)

    def add(t, kind, emoji=None, cap=99, spacing=0.0, force=False, **extra):
        if t < 0 or t > total - 0.05 or counts.get(kind, 0) >= cap:
            return False
        if not force and (not free(t) or sum(1 for f in fx if f["kind"] not in STRUCT) >= budget):
            return False
        if spacing and any(f["kind"] == kind and abs(t - f["t"]) < spacing for f in fx):
            return False
        fx.append({"t": round(t, 3), "kind": kind, **({"emoji": emoji} if emoji else {}), **extra})
        counts[kind] = counts.get(kind, 0) + 1
        return True

    # 1) structure du Short (toujours là, hors budget)
    if hook:
        add(0.0, "whoosh", force=True)
        add(0.12, "glitch", force=True)
    if card_at is not None and card_at < total:
        add(max(card_at - 1.15, 0.5), "riser", force=True, dur=1.15)
        add(card_at, "whoosh", force=True)
    if question_at is not None:
        add(question_at, "ding", force=True)

    speech = [w for w in words if w["s"] < (card_at if card_at is not None else body) - 0.3
              and not re.fullmatch(r"\[[^\]]*\]", w["w"])]
    # 2) rires (transcription) → boing + 😂
    for w in words:
        if LAUGH.search(w["w"]):
            add(w["s"], "boing", "😂", cap=3, spacing=4.0)
    # 3) pics de voix → boom (punch-in, tremblement, flash) ; le plus fort → basse
    peaks = loud_peaks(base, body, n=ed.get("sfx_max_boom", 3) + 1)
    for i, (t, sc) in enumerate(peaks):
        add(t, "bass" if i == 0 and sc > 14 else "boom", cap=ed.get("sfx_max_boom", 3) + 1, spacing=4.0)
    # 4) mots qui appellent un son précis
    for w in speech:
        if MONEY.search(w["w"]):
            add(w["s"], "kaching", "💰", cap=2, spacing=5.0)
        elif FAIL.search(w["w"]):
            add(w["s"], "fail", "📉", cap=1)
        elif STOP.match(w["w"].strip()):
            add(w["s"], "scratch", cap=1)
    # 5) emojis sur les autres mots forts (pop)
    for w in speech:
        e = _emoji(w["w"])
        if e and counts.get("pop", 0) < ed.get("emoji_max", 5):
            add(w["s"], "pop", e, spacing=ed.get("emoji_gap", 2.5))
    for w in speech:  # chiffres / mots-clés colorés sans emoji : petit tick
        if keyword_re and keyword_re.search(w["w"]):
            add(w["s"], "tick", cap=3, spacing=3.0)
    # 6) swish discret sur les changements de plan, s'il reste de la place
    off = 0.0
    for i, (a, b, _z) in enumerate(segs):
        if i and off < body - 0.5:
            add(off - 0.05, "swish", spacing=2.0)
        off += b - a
    return sorted((f for f in fx if f["kind"] != "_reserve"), key=lambda f: f["t"])


STRUCT = {"whoosh", "glitch", "riser", "ding", "_reserve"}


def plan_fx_tension(base: Path, words, segs, body, total, ed, hook=True, card_at=None,
                    question_at=None, keyword_re=None, reserved=()):
    """Style « tension » (toutes les chaînes sauf Clip by Cut) : rien de comique. Les sons
    accentuent ce qui se passe et donnent envie de rester : impact sur les pics de voix,
    aspiration qui annonce les mots forts, swish sur les plans, riser avant la fin.
    Pas d'emoji, pas de boing / ka-ching / trombone / scratch."""
    gap = ed.get("sfx_min_gap", 0.7)
    budget = max(int(body / ed.get("sfx_density", 2.6)), 3)
    fx = [{"t": t, "kind": "_reserve"} for t in reserved]
    counts = {}

    def add(t, kind, cap=99, spacing=0.0, force=False, **extra):
        if t < 0 or t > total - 0.05 or counts.get(kind, 0) >= cap:
            return False
        if not force:
            if any(abs(t - f["t"]) < gap for f in fx):
                return False
            if sum(1 for f in fx if f["kind"] not in STRUCT_T) >= budget:
                return False
        if spacing and any(f["kind"] == kind and abs(t - f["t"]) < spacing for f in fx):
            return False
        fx.append({"t": round(t, 3), "kind": kind, **extra})
        counts[kind] = counts.get(kind, 0) + 1
        return True

    if hook:
        add(0.0, "hit", force=True)
        add(0.05, "whoosh", force=True)
    if card_at is not None and card_at < total:
        add(max(card_at - 1.15, 0.5), "riser", force=True, dur=1.15)
        add(card_at, "hit", force=True)
    if question_at is not None:
        add(question_at, "swish", force=True)

    end = (card_at if card_at is not None else body) - 0.3
    speech = [w for w in words if 0.8 < w["s"] < end and not re.fullmatch(r"\[[^\]]*\]", w["w"])]
    # pics de voix : impact + punch-in + vignette
    for t, _sc in loud_peaks(base, body, n=ed.get("sfx_max_boom", 3) + 1, min_gap=5.0):
        add(t, "hit", cap=ed.get("sfx_max_boom", 3) + 2, spacing=4.0)
    # mots forts (chiffres, argent, mort, jamais...) : aspiration qui monte jusqu'au mot
    strong = [w for w in speech if (keyword_re and keyword_re.search(w["w"]))
              or MONEY.search(w["w"]) or FAIL.search(w["w"]) or STOP.match(w["w"].strip())]
    for w in strong:
        if w["s"] > 1.2:
            add(w["s"] - 0.7, "reverse", cap=3, spacing=6.0, dur=0.7)
    for w in strong:   # petit tick discret sur les autres
        add(w["s"], "tick", cap=3, spacing=3.0)
    off = 0.0
    for i, (a, b, _z) in enumerate(segs):
        if i and off < body - 0.5:
            add(off - 0.05, "swish", spacing=2.5)
        off += b - a
    return sorted((f for f in fx if f["kind"] != "_reserve"), key=lambda f: f["t"])


STRUCT_T = {"whoosh", "riser", "_reserve"}


ENGAGE_LABELS = {"fr": ("S'ABONNER", "ABONNÉ"), "en": ("SUBSCRIBE", "SUBSCRIBED"),
                 "es": ("SUSCRÍBETE", "SUSCRITO")}


def engage(body, st, ed, lang="fr", card_at=None):
    """Animations « abonne-toi » (bouton + curseur + cloche) et « like » (cœur + pouce)."""
    if not ed.get("engage_anim", True) or body < 8:
        return None
    sub, done = (st.get("subscribe_labels") or ENGAGE_LABELS.get(lang, ENGAGE_LABELS["en"]))
    t_sub = max(st["hook_seconds"] + 0.8, body * ed.get("subscribe_at", 0.4))
    t_like = max(t_sub + 4.0, body * ed.get("like_at", 0.72))
    end = (card_at if card_at is not None else body) - 0.6
    out = {"subscribe": None, "like": None}
    if t_sub + 2.6 <= end:
        out["subscribe"] = {"at": round(t_sub, 2), "label": sub, "done": done}
    if t_like + 1.6 <= end:
        out["like"] = {"at": round(t_like, 2)}
    return out if out["subscribe"] or out["like"] else None


def build_props(base: Path, *, words, segs, body, total, st, ed, variant_colors, hook, credit,
                takeaway, outro_at, question, question_at, keyword_re):
    v = variant_colors
    cuts, off = [], 0.0
    for a, b, _z in segs:  # temps de sortie de chaque changement de plan
        if off > 0:
            cuts.append(round(off, 3))
        off += b - a
    out_words = [{"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3),
                  "key": bool(keyword_re and keyword_re.search(w["w"]))} for w in words]
    cta = st.get("cta") or {}
    wm = st.get("watermark") or {}
    props = {
        "fps": 30, "total": round(total, 3), "body": round(body, 3),
        "video": "local:" + str(base.resolve()),
        "font": st["font"],
        "colors": {"active": _ass_rgb(v["active"]), "key": _ass_rgb(v["key"]),
                   "hookText": _ass_rgb(v["hook_text"]), "hookBack": _ass_rgb(v["hook_back"]),
                   "bar": _ffmpeg_rgb(v["bar"]), "card": _ass_rgb(st.get("card_back", "&H00141414"))},
        "sub": {"size": st["sub_size"], "marginBottom": st["sub_margin_bottom"],
                "wordsPerLine": st["words_per_line"]},
        "hook": {"text": hook, "seconds": st["hook_seconds"], "size": st["hook_size"]} if hook else None,
        "credit": credit or None,
        "card": {"label": st.get("end_card_label", "À RETENIR"), "text": takeaway, "at": round(outro_at, 3)}
        if takeaway and outro_at is not None else None,
        "question": {"text": question, "at": round(question_at, 3)} if question else None,
        "words": out_words,
        "fx": [],
        "cuts": cuts,
        "sfx": sfx_files() if ed["sfx"] else {},
        "sfxVolume": ed["sfx_volume"],
        "progressBar": bool(ed["progress_bar"]),
        "intensity": ed.get("fx_intensity", 1.0),
        "cta": None, "watermark": None,
        "engage": engage(body, st, ed, lang=ed.get("_lang", "fr"),
                         card_at=outro_at if takeaway else None),
        "style": ed.get("fx_style", "funny"),
    }
    eng = props["engage"] or {}
    reserved = ([eng["subscribe"]["at"], eng["subscribe"]["at"] + 1.0] if eng.get("subscribe") else []) +                ([eng["like"]["at"]] if eng.get("like") else [])
    if ed["sfx"]:
        props["fx"] = plan_fx(base, words, segs, body, total, ed, hook=bool(hook),
                              card_at=outro_at if takeaway else None, question_at=question_at,
                              keyword_re=keyword_re, reserved=reserved)
    if cta.get("text"):
        props["cta"] = {"text": cta["text"], "size": cta.get("size", 46)}
        if cta.get("position", "top") == "top":
            props["cta"]["top"] = cta.get("y_from_top", 230)
        else:
            props["cta"]["bottom"] = cta.get("y_from_bottom", 420)
    if wm.get("file"):
        f = ROOT / wm["file"]
        if not f.exists():
            raise SystemExit(f"Watermark introuvable : {f} (exigé par la campagne)")
        props["watermark"] = {"src": "local:" + str(f.resolve()),
                              "width": int(1080 * wm.get("width", 0.4)) // 2 * 2,
                              "opacity": wm.get("opacity", 0.9)}
        if wm.get("position") == "top":
            props["watermark"]["top"] = wm.get("y_from_top", 150)
        else:
            props["watermark"]["bottom"] = wm.get("y_from_bottom", 380)
    return props


def available():
    return (RM_DIR / "node_modules" / "@remotion" / "renderer").exists()


def run(props, out: Path, timeout=1800):
    """Lance le rendu Remotion. Lève une exception en cas d'échec."""
    pj = out.with_suffix(".props.json")
    pj.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    try:
        r = subprocess.run(["node", str(RM_DIR / "render.mjs"), str(pj.resolve()), str(out.resolve())],
                           cwd=RM_DIR, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-600:])
        last = [l for l in r.stdout.replace("\r", "\n").splitlines() if l.strip()]
        if last:
            print(last[-1])
    finally:
        if not os.environ.get("RM_KEEP"):
            pj.unlink(missing_ok=True)
    return out
