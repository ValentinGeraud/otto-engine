"""Montage 9:16 « transformé » (ce que YouTube attend pour monétiser du contenu repris) :

- jump cuts : les blancs de plus de `max_gap` s sont coupés (rythme plus dense) ;
- punch-in : le cadrage alterne plan normal / plan serré à chaque coupe (cache la coupe) ;
- étalonnage léger (contraste, saturation) ;
- hook animé en haut, crédit de la source, sous-titres mot à mot avec mots-clés en couleur ;
- barre de progression (pousse à regarder jusqu'au bout) ;
- outro commentée : le verdict de la chaîne LU EN VOIX OFF (edge-tts, gratuit) sur fond flou ;
- musique de fond optionnelle (fichiers déposés dans branding/music/).
"""
import asyncio
import json
import random
import re
import statistics
import subprocess
from pathlib import Path

import cv2

W, H = 1080, 1920
ROOT = Path(__file__).parent

EDIT = {  # surchargeable dans config.yaml > style > edit
    "jump_cuts": True, "max_gap": 0.35, "silence_rel_db": 10,
    "punch_zoom": 1.12, "zoom_min_block": 2.0, "max_shot": 3.5,
    "grade": True,
    "keyword_pop": True,
    "credit": True,
    "progress_bar": True,
    # fin du short : carte verdict + animations « s'abonner » et « like », SANS voix off (14/11/2026)
    "outro_voice": False, "voice": None, "voice_rate": "+8%", "engage_end": True,
    "music_dir": "branding/music", "music_volume": 0.07,
    "sfx": False, "sfx_volume": 0.5, "sfx_flash": True,  # bruitages (voir sfx.py)
    "question_seconds": 2.2,  # temps de lecture de la question après la voix off
    # passe Remotion (remotion_fx.py) : zooms, punch-in, tremblements, emojis, bruitages
    "remotion": True, "fx_intensity": 1.0, "emoji_max": 5, "emoji_gap": 2.5,
}
VOICES = {"fr": "fr-FR-HenriNeural", "en": "en-US-AndrewNeural", "es": "es-MX-JorgeNeural"}
CREDIT = {"fr": "Source", "en": "Source", "es": "Fuente"}
KEYWORDS = re.compile(
    r"\d|[$€£%]|million|milliard|billion|trillion|millón|millones|mil|argent|money|dinero|"
    r"euros?|dollars?|dólares?|mort|death|muerte|muerto|jamais|never|nunca|secret|secreto|"
    r"police|policía|prison|cárcel|crash|fraud|fraude|rich|riche|rico|broke|fauché", re.I)


def probe(video: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


def duration(path: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def face_center_x(video: Path, start, end):
    """Médiane de la position horizontale des visages sur le clip, ou None."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(video))
    xs, samples, t = [], 0, start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        t += 1.0
        if not ok:
            continue
        samples += 1
        small = cv2.resize(frame, None, fx=0.5, fy=0.5)
        faces = cascade.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 1.1, 5,
                                         minSize=(40, 40))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # plus grand visage
            xs.append((x + w / 2) * 2)
    cap.release()
    if samples and len(xs) / samples >= 0.4:
        return statistics.median(xs)
    return None


def _ts(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _esc(s):
    return s.replace("{", "(").replace("}", ")").replace("\n", " ")


# Variantes visuelles : évite que toutes les vidéos aient exactement le même gabarit
# trajets du cadre pendant un plan zoomé : (x, y) de départ → d'arrivée, en fraction de la marge
DRIFTS = [((0.15, 0.5), (0.85, 0.5)), ((0.85, 0.4), (0.15, 0.6)), ((0.5, 0.1), (0.5, 0.9)),
          ((0.5, 0.5), (0.5, 0.5)), ((0.2, 0.8), (0.8, 0.2))]

VARIANTS = [
    {"active": "00E5FF", "key": "365AFF", "hook_back": "&H00000000", "hook_text": "&H00FFFFFF",
     "bar": "0xFFE500"},  # jaune / noir
    {"active": "5CFF39", "key": "00C8FF", "hook_back": "&H00FFFFFF", "hook_text": "&H00000000",
     "bar": "0x39FF5C"},  # vert / blanc
    {"active": "FFC800", "key": "00E5FF", "hook_back": "&H00000000", "hook_text": "&H0000E5FF",
     "bar": "0x00C8FF"},  # bleu / hook jaune
]


def _variants(st):
    """Palettes du gabarit. Otto Clip : la couleur choisie dans le SaaS (style.active_color, BBGGRR)
    remplace celle du gabarit principal ; les autres gabarits tournent toujours (anti-répétition)."""
    vs = [dict(v) for v in (st.get("variants") or VARIANTS)]
    c = st.get("active_color")
    if c and len(c) == 6:
        vs[0]["active"], vs[0]["bar"] = c, "0x" + c[4:6] + c[2:4] + c[0:2]
    return vs


# ---------- découpe : jump cuts + punch-in ----------
def speech_spans(video: Path, start, end, ed):
    """Passages parlés d'après le SON (ffmpeg silencedetect), en temps source.
    Plus fiable que les horodatages des sous-titres YouTube, qui collent les mots entre eux."""
    import numpy as np
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}",
                              "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                             capture_output=True, timeout=120).stdout
    except Exception:
        return None
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    win = 800  # tranches de 50 ms
    n = len(x) // win
    if n < 10:
        return None
    rms = 20 * np.log10(np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(1)) + 1e-9)
    # seuil RELATIF au volume de la vidéo (les podcasts compressés n'ont jamais de vrai silence)
    low = rms < np.percentile(rms, 90) - ed["silence_rel_db"]
    need = max(int(ed["max_gap"] / 0.05), 1)
    spans, i = [], 0
    cur = 0  # début du passage parlé courant (en tranches)
    while i < n:
        if low[i]:
            j = i
            while j < n and low[j]:
                j += 1
            if j - i >= need:  # vraie pause : on la retire
                if i > cur:
                    spans.append([start + cur * 0.05, start + i * 0.05])
                cur = j
            i = j
        else:
            i += 1
    if cur < n:
        spans.append([start + cur * 0.05, end])
    return [sp for sp in spans if sp[1] - sp[0] >= 0.12] or None


def _split_long(segs, words, max_shot):
    """Coupe les plans trop longs au début d'un mot : le cadrage change même sans pause."""
    starts = sorted(w["s"] for w in words)
    out = []
    for a, b in segs:
        while b - a > max_shot * 1.4:
            cands = [t for t in starts if a + max_shot * 0.7 <= t <= a + max_shot * 1.3]
            t = min(cands, key=lambda t: abs(t - a - max_shot)) if cands else a + max_shot
            out.append([a, t])
            a = t
        out.append([a, b])
    return out


def plan_segments(words, start, end, ed, spans=None):
    """Passages gardés [(a, b, zoom)] en temps source, blancs retirés.
    spans = passages parlés détectés dans le son ; sinon on se base sur les écarts entre mots."""
    if not ed["jump_cuts"] or (not words and not spans):
        return [(start, end, 1.0)]
    segs = []
    if spans and len(spans) > 1:
        segs = [list(sp) for sp in spans]
    else:
        for w in words:
            if segs and w["s"] - segs[-1][1] <= ed["max_gap"]:
                segs[-1][1] = max(segs[-1][1], w["e"])
            else:
                segs.append([w["s"], w["e"]])
    padded = []
    for a, b in segs:  # un peu d'air autour des mots pour ne pas couper les syllabes
        a, b = max(a - 0.08, start), min(b + 0.15, end)
        if padded and a <= padded[-1][1]:
            padded[-1][1] = max(padded[-1][1], b)
        else:
            padded.append([a, b])
    if ed.get("max_shot"):
        padded = _split_long(padded, words, ed["max_shot"])
    # niveaux de zoom parcourus en boucle (par défaut : plan large / plan serré en alternance)
    levels = ed.get("zoom_levels") or [1.0, ed["punch_zoom"]]
    out, li, block = [], 0, 0.0
    z = levels[0]
    for i, (a, b) in enumerate(padded):
        if i and block >= ed["zoom_min_block"]:  # on change de cadrage sur la coupe
            li, block = (li + 1) % len(levels), 0.0
            z = levels[li]
        out.append((a, b, z))
        block += b - a
    return out


def remap(words, segs):
    """Mots en temps de sortie (après suppression des blancs)."""
    res, off = [], 0.0
    for a, b, _ in segs:
        for w in words:
            if a - 0.01 <= w["s"] < b:
                res.append({"w": w["w"], "s": off + w["s"] - a, "e": off + min(w["e"], b) - a})
        off += b - a
    return res, off


# ---------- sous-titres, hook, crédit, verdict ----------
def build_ass(words, total, hook, st, path: Path, takeaway="", variant=0,
              outro_at=None, credit="", lang="fr", ed=EDIT, question="", question_at=None):
    font = st["font"]
    vs = _variants(st)   # palette propre à la chaîne (ex. GTA 6) sinon celle par défaut
    v = vs[variant % len(vs)]
    card = st.get("card_back", "&H00141414")   # fond de la carte de fin (verdict, question)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{st['sub_size']},&H00{st['sub_color']},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,80,80,{st['sub_margin_bottom']},1
Style: Hook,{font},{st['hook_size']},{v['hook_text']},&H00FFFFFF,{v['hook_back']},&H00000000,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,260,1
Style: Take,{font},{int(st['hook_size'] * 1.1)},&H00FFFFFF,&H00FFFFFF,{card},&H00000000,-1,0,0,0,100,100,0,0,3,22,0,5,110,110,0,1
Style: Question,{font},{int(st['hook_size'] * 0.95)},&H00FFFFFF,&H00FFFFFF,{card},&H00000000,-1,0,0,0,100,100,0,0,3,18,0,2,110,110,560,1
Style: Credit,Arial,34,&H30FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,8,90,90,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:  # apparition avec un léger rebond
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(st['hook_seconds'])},Hook,,0,0,0,,"
                     r"{\fad(80,200)\fscx85\fscy85\t(0,180,\fscx100\fscy100)}" + _esc(hook))
    if credit and ed["credit"]:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(total, 4))},Credit,,0,0,0,,"
                     r"{\fad(0,300)}" + f"{CREDIT.get(lang, 'Source')} : {_esc(credit)}")

    clip_words = [w for w in words if not re.fullmatch(r"\[[^\]]*\]", w["w"])]
    n = st["words_per_line"]
    chunks, cur = [], []
    for w in clip_words:
        if cur and (len(cur) >= n or w["s"] - cur[-1]["e"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)

    active, key = v["active"], v["key"]
    if takeaway:
        a = outro_at if outro_at is not None else max(total - 3.0, 0)
        lines.append(f"Dialogue: 2,{_ts(a)},{_ts(total)},Take,,0,0,0,,"
                     r"{\fad(150,0)\fscx90\fscy90\t(0,200,\fscx100\fscy100)}"
                     f"{{\\c&H00{active}&}}{st.get('end_card_label', 'À RETENIR')}{{\\r}}"
                     f"\\N{_esc(takeaway)}")
    if question:  # question au spectateur : écrite sous le verdict, jamais lue
        qa = question_at if question_at is not None else max(total - 2.0, 0)
        lines.append(f"Dialogue: 2,{_ts(qa)},{_ts(total)},Question,,0,0,0,,"
                     r"{\fad(120,0)\fscx70\fscy70\t(0,160,\fscx105\fscy105)\t(160,260,\fscx100\fscy100)}"
                     f"{{\\c&H00{active}&}}{_esc(question)}")
    sub_end = outro_at if outro_at is not None else total
    for chunk in chunks:
        for i, w in enumerate(chunk):
            a = w["s"]
            if a >= sub_end:
                break
            b = min(chunk[i + 1]["s"] if i + 1 < len(chunk) else w["e"], sub_end)
            parts = []
            for j, x in enumerate(chunk):
                txt = _esc(x["w"]).upper()
                is_key = ed["keyword_pop"] and KEYWORDS.search(x["w"])
                if j == i:  # mot prononcé : couleur + léger grossissement
                    parts.append(f"{{\\c&H00{key if is_key else active}&\\fscx112\\fscy112}}"
                                 f"{txt}{{\\r}}")
                elif is_key:
                    parts.append(f"{{\\c&H00{key}&}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            lines.append(f"Dialogue: 0,{_ts(a)},{_ts(max(b, a + 0.05))},Sub,,0,0,0,,"
                         + " ".join(parts))
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- voix off ----------
def tts(text, voice, rate, path: Path):
    """Voix off gratuite (voix neuronales Microsoft via edge-tts). Durée, ou None si échec."""
    import time
    err = None
    for attempt in range(4):  # le service renvoie parfois « No audio was received » : on réessaie
        try:
            import edge_tts
            asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(path)))
            d = duration(path)
            if d:
                return d
            err = "fichier audio vide"
        except Exception as e:
            err = str(e)[:80]
        time.sleep(2 + 3 * attempt)
    print(f"    (voix off indisponible après 4 essais : {err})")
    return None


def _music(ed, variant):
    d = ROOT / ed["music_dir"]
    files = sorted(p for p in d.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")) \
        if d.exists() else []
    return random.Random(variant).choice(files) if files else None


# ---------- rendu ----------
def render(video: Path, words, clip, st, out: Path, lang="fr", person=None):
    st0 = st
    ed = {**EDIT, **(st.get("edit") or {})}
    if clip.get("action"):
        # action de jeu : on ne coupe PAS les silences (sinon on coupe l'action elle-même, où personne ne parle)
        ed["jump_cuts"] = False
    use_rm = False
    if ed["remotion"]:
        import remotion_fx
        use_rm = remotion_fx.available()
        if not use_rm:
            print("    (Remotion non installé : lance « npm install » dans clipper/remotion)")
    # On démarre pile sur le premier mot (pas de blanc au début)
    first = next((w for w in words if w["s"] >= clip["start"] - 0.3), None)
    start = first["s"] - 0.05 if first and first["s"] - clip["start"] < 2 else clip["start"]
    end = clip["end"] + 0.3
    out.parent.mkdir(parents=True, exist_ok=True)

    src_words = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]
    spans = speech_spans(video, start, end, ed) if ed["jump_cuts"] else None
    segs = plan_segments(src_words, start, end, ed, spans)
    out_words, body = remap(src_words, segs)

    # outro : verdict lu en voix off sur l'image figée floutée
    takeaway = clip.get("takeaway", "")
    voice_file, outro = None, 0.0
    if takeaway:
        if ed["outro_voice"]:
            voice_file = out.with_suffix(".voice.mp3")
            d = tts(takeaway, ed["voice"] or VOICES.get(lang, VOICES["en"]), ed["voice_rate"],
                    voice_file)
            if d:
                outro = d + 0.6
            else:
                voice_file = None
        if not outro:
            outro = 2.5
    # question au spectateur : apparaît quand la voix off a fini (elle n'est pas lue)
    question = clip.get("question", "") if takeaway else ""
    question_at = None
    if ed.get("end_question") and clip.get("question"):
        # fin = la question seule, en grand (« ET TOI ? »), sans verdict ni voix off
        takeaway, question = clip["question"], ""
        st = {**st, "end_card_label": st.get("question_label") or
              {"en": "WHAT ABOUT YOU?", "es": "¿Y TÚ?"}.get(lang, "ET TOI ?")}
        if voice_file:
            voice_file.unlink(missing_ok=True)
            voice_file = None
        outro = ed.get("question_card_seconds", 2.8)
    if question:
        question_at = body + outro - 0.3
        outro += ed["question_seconds"]
    if outro and ed.get("engage_end", True) and ed.get("engage_anim", True):
        outro = max(outro, 4.7)   # place pour « s'abonner » (2,6 s) puis « like » (1,6 s)
    total = body + outro

    ass = out.with_suffix(".ass")
    build_ass(out_words, total, clip.get("hook", ""), st, ass, takeaway=takeaway,
              variant=clip.get("variant", 0), outro_at=body if takeaway else None,
              credit=clip.get("credit", ""), lang=lang, ed=ed,
              question=question, question_at=question_at)

    # bruitages
    sfx_file, sfx_events = None, []
    if ed["sfx"] and not use_rm:  # avec Remotion, les sons sont placés par remotion_fx
        try:
            import sfx
            sfx_events = sfx.plan(out_words, segs, body, total, ed, hook=bool(clip.get("hook")),
                                  question_at=question_at,
                                  keyword_re=KEYWORDS if ed["keyword_pop"] else None)
            if sfx_events:
                sfx_file = sfx.build_track(sfx_events, total, out.with_suffix(".sfx.wav"),
                                           ed["sfx_volume"])
        except Exception as e:
            print(f"    (bruitages ignorés : {str(e)[:80]})")
            sfx_file, sfx_events = None, []

    # cadrage
    sw, sh = probe(video)
    layout = st.get("layout", "auto")
    seg_cx = {}
    if person is not None and sw > sh:
        # personne imposée (campagne) : le cadre la suit plan par plan ; si elle n'est pas
        # reconnue sur un plan (plan large, profil), on garde sa position médiane
        found = []
        for i, (a, b, _z) in enumerate(segs):
            xs = [x for x in person.track(video, a, b, max((b - a) / 3, 0.4)) if x is not None]
            if xs:
                seg_cx[i] = statistics.median(xs)
                found.append(seg_cx[i])
        cx = statistics.median(found) if found else None
        # plan sans la personne (ex. gros plan sur l'invitée) : on cadre sur le visage présent
        # plutôt que sur un décor vide ; plan large sans visage net : position médiane
        for i, (a, b, _z) in enumerate(segs):
            if i not in seg_cx:
                xs = [x for x in (person.any_face(video, a + (b - a) * k / 4) for k in (1, 2, 3))
                      if x is not None]
                if xs:
                    seg_cx[i] = statistics.median(xs)
    else:
        cx = face_center_x(video, start, end) if layout in ("auto", "crop") and sw > sh else None

    def frame(src, tag, x_center=None):
        """Chaîne qui transforme [src] en image 1080x1920 étiquetée [tag]."""
        x_center = x_center if x_center is not None else cx
        if sw <= sh * 0.6:  # déjà vertical
            return f"[{src}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[{tag}]"
        if x_center is not None or layout == "crop":
            cw = int(sh * 9 / 16) // 2 * 2
            x = int(min(max((x_center or sw / 2) - cw / 2, 0), sw - cw))
            return f"[{src}]crop={cw}:{sh}:{x}:0,scale={W}:{H}[{tag}]"
        return (f"[{src}]split[{tag}a][{tag}b];[{tag}a]scale={W}:{H}:force_original_aspect_ratio="
                f"increase,crop={W}:{H},boxblur=25:2,eq=brightness=-0.15[{tag}bg];"
                f"[{tag}b]scale={W}:-2[{tag}fg];[{tag}bg][{tag}fg]overlay=(W-w)/2:(H-h)/2[{tag}]")

    grade = ",eq=contrast=1.06:saturation=1.15" if ed["grade"] else ""
    norm_v = f"fps=30,setsar=1,format=yuv420p"
    norm_a = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    t0 = max(start - 0.5, 0)  # l'entrée est lue à partir de t0 : temps relatifs
    g, cat = [], []
    for i, (a, b, z) in enumerate(segs):
        a, b = a - t0, b - t0
        g.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[s{i}]")
        g.append(frame(f"s{i}", f"f{i}", seg_cx.get(i)))
        zoom = (f"scale={int(W * z) // 2 * 2}:{int(H * z) // 2 * 2},crop={W}:{H}," if z > 1 else "")
        if z > 1 and ed.get("zoom_drift"):
            # le cadre glisse pendant le plan (gauche → droite, droite → gauche, haut → bas…)
            (x0, y0), (x1, y1) = DRIFTS[i % len(DRIFTS)]
            d = max(b - a, 0.2)
            zoom = (f"scale={int(W * z) // 2 * 2}:{int(H * z) // 2 * 2},"
                    f"crop={W}:{H}:x='(iw-ow)*({x0}+({x1 - x0})*min(t/{d:.3f},1))'"
                    f":y='(ih-oh)*({y0}+({y1 - y0})*min(t/{d:.3f},1))',")
        g.append(f"[f{i}]{zoom}{norm_v}{grade}[v{i}]")
        # fondus seulement sur les vraies coupes (pas sur un simple changement de cadrage)
        cut_in = i == 0 or segs[i - 1][1] < segs[i][0] - 1e-3
        cut_out = i == len(segs) - 1 or segs[i + 1][0] > segs[i][1] + 1e-3
        fades = ("afade=t=in:d=0.015," if cut_in else "") + \
                (f"afade=t=out:st={max(b - a - 0.03, 0):.3f}:d=0.03," if cut_out else "")
        g.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,{norm_a},"
                 f"{fades}anull[a{i}]")
        cat += [f"[v{i}]", f"[a{i}]"]
    inputs = ["-ss", f"{t0:.2f}", "-t", f"{end - t0 + 0.5:.2f}", "-i", str(video.resolve())]
    n = len(segs)
    if outro:
        lb = segs[-1][1] - t0
        g.append(f"[0:v]trim=start={max(lb - 0.12, 0):.3f}:end={lb:.3f},setpts=PTS-STARTPTS[so]")
        g.append(frame("so", "fo", seg_cx.get(len(segs) - 1)))
        g.append(f"[fo]boxblur=22:2,eq=brightness=-0.28,{norm_v},"
                 f"tpad=stop_mode=clone:stop_duration={outro:.2f},trim=duration={outro:.2f}[vo]")
        if voice_file:
            inputs += ["-i", str(voice_file.resolve())]
            g.append(f"[1:a]{norm_a},adelay=250:all=1,apad=whole_dur={outro:.2f}[ao]")
        else:
            g.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={outro:.2f},{norm_a}[ao]")
        cat += ["[vo]", "[ao]"]
        n += 1
    g.append("".join(cat) + f"concat=n={n}:v=1:a=1[vc][ac]")

    # avec Remotion, le clip de base sort sans texte ni habillage : tout est ajouté ensuite
    post = "null" if use_rm else f"ass={ass.name}"
    if ed["progress_bar"] and not use_rm:
        vs = _variants(st)
        bar = vs[clip.get("variant", 0) % len(vs)]["bar"]
        post += (f",drawbox=x=0:y=0:w='iw*min(t/{body:.2f},1)':h=12:"
                 f"color={bar}@0.95:t=fill")
    if ed["sfx_flash"] and not use_rm:  # flash blanc bref sur chaque « boom »
        for t, name in sfx_events:
            if name == "boom":
                post += (f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.35:t=fill:"
                         f"enable='between(t,{t:.3f},{t + 0.07:.3f})'")
    cta = {} if use_rm else st.get("cta") or {}
    if cta.get("text"):
        # texte imposé par une campagne, affiché pendant tout le clip (ex. « Watch the full
        # documentary on Netflix ») : bandeau lisible, centré, au-dessus des boutons de l'appli
        font = cta.get("font", "C\\:/Windows/Fonts/arialbd.ttf" if __import__("os").name == "nt"
                       else "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf")
        txt = cta["text"].replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
        y = cta.get("y_from_top", 230) if cta.get("position", "top") == "top" \
            else f"h-th-{cta.get('y_from_bottom', 420)}"
        post += (f",drawtext=fontfile='{font}':text='{txt}':fontsize={cta.get('size', 46)}:"
                 f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=18:"
                 f"x=(w-tw)/2:y={y}")
    wm = {} if use_rm else st.get("watermark") or {}
    wm_file = (ROOT / wm["file"]) if wm.get("file") else None
    if wm_file and not wm_file.exists():
        raise SystemExit(f"Watermark introuvable : {wm_file} (exigé par la campagne)")
    if wm_file:
        # watermark imposé par une campagne : centré horizontalement, taille raisonnable,
        # jamais collé au bord (top = sous la barre de progression, bottom = au-dessus des boutons)
        idx = inputs.count("-i")
        inputs += ["-loop", "1", "-i", str(wm_file.resolve())]
        ww = int(W * wm.get("width", 0.4)) // 2 * 2
        y = wm.get("y_from_top", 150) if wm.get("position") == "top" \
            else f"H-h-{wm.get('y_from_bottom', 380)}"
        g.append(f"[vc]{post}[vpost]")
        g.append(f"[{idx}:v]scale={ww}:-2,format=rgba,colorchannelmixer=aa={wm.get('opacity', 0.9)}[wm]")
        g.append(f"[vpost][wm]overlay=(W-w)/2:{y}:shortest=1[vout]")
    else:
        g.append(f"[vc]{post}[vout]")
    g.append("[ac]loudnorm=I=-14:TP=-1.5[an]")
    aout = "[an]"
    if sfx_file:
        idx = inputs.count("-i")
        inputs += ["-i", str(sfx_file.resolve())]
        g.append(f"[{idx}:a]{norm_a}[fx]")
        g.append(f"{aout}[fx]amix=inputs=2:duration=first:normalize=0[afx]")
        aout = "[afx]"
    music = _music(ed, clip.get("variant", 0))
    if music:
        idx = inputs.count("-i")
        inputs += ["-stream_loop", "-1", "-i", str(music.resolve())]
        g.append(f"[{idx}:a]{norm_a},volume={ed['music_volume']},atrim=duration={total:.2f},"
                 f"afade=t=out:st={max(total - 1, 0):.2f}:d=1[mu]")
        g.append(f"{aout}[mu]amix=inputs=2:duration=first:normalize=0[amu]")
        aout = "[amu]"
    if sfx_file:  # évite la saturation quand voix + boom se superposent
        g.append(f"{aout}alimiter=limit=0.95:level=disabled[alim]")
        aout = "[alim]"

    script = out.with_suffix(".filter.txt")
    script.write_text(";\n".join(g), encoding="utf-8")
    target = out.with_suffix(".base.mp4") if use_rm else out
    quality = ["-preset", "veryfast", "-crf", "16"] if use_rm else ["-preset", "medium", "-crf", "20"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-/filter_complex", script.name, "-map", "[vout]", "-map", aout,
           "-c:v", "libx264", *quality, "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", target.name]
    # cwd = dossier de sortie : évite les soucis de chemin Windows dans le filtre ass
    subprocess.run(cmd, cwd=out.parent, check=True)
    script.unlink(missing_ok=True)
    if voice_file:
        voice_file.unlink(missing_ok=True)
    if sfx_file:
        sfx_file.unlink(missing_ok=True)
    if use_rm:
        vs = _variants(st)
        try:
            props = remotion_fx.build_props(
                target, words=out_words, segs=segs, body=body, total=total, st=st,
                ed={**ed, "_lang": lang},
                variant_colors=vs[clip.get("variant", 0) % len(vs)], hook=clip.get("hook", ""),
                credit=(f"{CREDIT.get(lang, 'Source')} : {clip['credit']}"
                        if clip.get("credit") and ed["credit"] else ""),
                takeaway=takeaway, outro_at=body if takeaway else None, question=question,
                question_at=question_at, keyword_re=KEYWORDS if ed["keyword_pop"] else None)
            sfx_events = [(f["t"], f["kind"]) for f in props["fx"]]
            remotion_fx.run(props, out)
        except Exception as e:
            print(f"    (Remotion en échec, montage ffmpeg classique : {str(e)[-300:]})")
            target.unlink(missing_ok=True)
            # secours ffmpeg : ses bruitages sont comiques, donc seulement pour le style « funny »
            return render(video, words, clip, {**st0, "edit": {
                **(st0.get("edit") or {}), "remotion": False,
                "sfx": ed["sfx"] and ed.get("fx_style", "funny") == "funny"}},
                          out, lang=lang, person=person)
        target.unlink(missing_ok=True)
        ass.unlink(missing_ok=True)
    cut = (end - start) - body
    fx = ""
    if sfx_events:
        names = [n for _, n in sfx_events]
        fx = ", bruitages " + " ".join(f"{n}×{names.count(n)}" for n in dict.fromkeys(names))
    print(f"    montage : {len(segs)} plans, {cut:.1f}s de blancs coupés, "
          f"outro {'voix off' if voice_file else 'muette' if outro else 'aucune'}"
          f"{', musique' if music else ''}{fx}{', Remotion' if use_rm else ''} → {total:.0f}s")
    return out
