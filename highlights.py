"""Choisit les meilleurs moments d'une transcription et écrit hook, titre et description."""
import json
import os
import re

PROMPT = """Tu es un clippeur expert en contenus courts viraux (TikTok, Reels, Shorts).
Niche du compte : {niche}.
À privilégier : {focus}

Voici la transcription horodatée d'une vidéo intitulée « {title} ».
Format : [début-fin] texte

{transcript}

Choisis les {n} MEILLEURS moments à transformer en clips verticaux.
Règles :
- Chaque clip dure entre {mn} et {mx} secondes.
- Il commence EXACTEMENT sur une phrase forte (pas d'intro, pas de « euh », pas de contexte),
  et finit sur une chute ou une phrase complète.
- Il doit se comprendre sans avoir vu le reste de la vidéo.
- Pas de chevauchement entre les clips.
- Note la viralité de chaque clip de 1 à 10, honnêtement.

Pour chaque clip, écris :
- "hook" : texte affiché en haut de l'écran pendant les {hook_s:.0f} premières secondes, 4 à 9 mots,
  percutant, qui crée une curiosité. Pas de point final, au maximum 1 emoji.{hook_style}
- "title" : titre YouTube Short de 60 caractères maximum.
- "caption" : description courte (1 phrase) suivie de 3 à 5 hashtags de NICHE (pas #fyp).
- "takeaway" : TON commentaire en 6 à 12 mots, affiché à la fin du clip : une punchline,
  un avis tranché ou une leçon. Il doit apporter quelque chose, pas résumer ce qu'on vient de voir.
IMPORTANT : hook, title, caption et takeaway sont écrits en {lang}, la langue du public de la chaîne.

Réponds UNIQUEMENT avec du JSON valide :
{{"clips": [{{"start": 12.3, "end": 48.9, "score": 8, "why": "...", "hook": "...", "title": "...", "caption": "...", "takeaway": "..."}}]}}"""


LANGS = {"fr": "français", "en": "anglais (natural American English)",
         "es": "espagnol (español neutro, compréhensible en Espagne et en Amérique latine)"}


def _lang(cfg):
    return LANGS.get(cfg.get("language", "fr"), cfg.get("language", "fr"))


def _hook_args(cfg):
    """Durée d'affichage et ton du hook, propres à chaque chaîne."""
    tone = " ".join((cfg.get("hook_style") or "").split())
    return {"hook_s": cfg.get("style", {}).get("hook_seconds", 3.0),
            "hook_style": f"\n  Ton du hook : {tone}" if tone else ""}


def _fmt(sents):
    return "\n".join(f"[{s['s']:.1f}-{s['e']:.1f}] {s['t']}" for s in sents)


def pick(sents, cfg, title, duration):
    mn, mx = cfg["min_seconds"], cfg["max_seconds"]
    # Source déjà courte (clip Twitch...) : on la garde en entier
    if duration and duration <= mx + 2:
        return [_whole(sents, title, duration)]
    ai = cfg.get("ai", "ollama")
    if ai == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        chunks, per_chunk, ask = [sents], cfg["clips_per_video"], _claude
    elif ai == "ollama":
        # un petit modèle local ne lit pas 1 h de transcription d'un coup :
        # on découpe en tranches de ~8 min, 2 moments par tranche, puis on garde les meilleurs
        chunks, cur = [], []
        for s in sents:
            if cur and s["e"] - cur[0]["s"] > 480:
                chunks.append(cur)
                cur = []
            cur.append(s)
        if cur:
            chunks.append(cur)
        per_chunk, ask = 2, _ollama
    else:
        return _heuristic(sents, cfg, title)

    clips, failed = [], 0
    for i, ch in enumerate(chunks):
        prompt = PROMPT.format(niche=cfg["niche"], focus=cfg.get("focus", ""), title=title,
                               **_hook_args(cfg),
                               lang=_lang(cfg),
                               transcript=_fmt(ch)[:300_000], n=per_chunk, mn=mn, mx=mx)
        try:
            data = json.loads(re.search(r"\{.*\}", ask(prompt, cfg), re.S).group(0))
            got = [c for c in data.get("clips", [])
                   if isinstance(c.get("start"), (int, float)) and isinstance(c.get("end"), (int, float))
                   and ch[0]["s"] - 5 <= c["start"] < c["end"] <= ch[-1]["e"] + 60]
            # durée hors bornes : on recadre au lieu de jeter
            for c in got:
                d = c["end"] - c["start"]
                if d < mn:
                    c["end"] = c["start"] + mn
                elif d > mx:
                    c["end"] = c["start"] + mx
            clips += got
        except Exception as e:
            failed += 1
            print(f"  (tranche {i + 1}/{len(chunks)} illisible : {str(e)[:60]})")
        if len(chunks) > 1:
            print(f"  … tranche {i + 1}/{len(chunks)} analysée ({len(clips)} moments trouvés)")
    if not clips:
        print("  (aucun moment exploitable → sélection heuristique)")
        return _heuristic(sents, cfg, title)
    clips.sort(key=lambda c: c.get("score", 0), reverse=True)
    picked = []
    for c in clips:  # pas de chevauchement
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
    if ask is _ollama:  # 2e passe : textes réécrits en ne lisant QUE le passage choisi
        for c in picked[: cfg["clips_per_video"]]:
            _polish(c, sents, cfg, title)
    return picked


POLISH = """Voici l'extrait exact d'une vidéo YouTube « {title} » (de {a:.0f}s à {b:.0f}s) :

{text}

Écris pour ce Short, en {lang} correct et naturel, sans faute :
- "hook" : 4 à 9 mots affichés en haut de l'écran pendant {hook_s:.0f} secondes, qui donnent envie
  de regarder la suite. Il doit correspondre à ce qui se passe VRAIMENT dans l'extrait.
  Pas de citation inventée.{hook_style}
- "title" : titre YouTube de 60 caractères maximum.
- "caption" : 1 phrase + 3 hashtags pertinents.
- "takeaway" : un commentaire drôle ou tranché de 6 à 12 mots sur ce moment.
- "score" : de 1 à 10, est-ce que ce moment est vraiment captivant sans contexte ?

Réponds UNIQUEMENT en JSON : {{"hook": "...", "title": "...", "caption": "...", "takeaway": "...", "score": 7}}"""


def _polish(c, sents, cfg, title):
    text = " ".join(s["t"] for s in sents if s["e"] > c["start"] and s["s"] < c["end"])
    try:
        d = json.loads(re.search(r"\{.*\}", _ollama(POLISH.format(
            title=title, a=c["start"], b=c["end"], text=text[:4000], lang=_lang(cfg),
            **_hook_args(cfg)), cfg),
            re.S).group(0))
        for k in ("hook", "title", "caption", "takeaway", "score"):
            if d.get(k):
                c[k] = d[k]
    except Exception as e:
        print(f"  (réécriture impossible : {str(e)[:60]})")


def _claude(prompt, cfg):
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=cfg.get("model", "claude-sonnet-5-5"), max_tokens=4000,
        messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if b.type == "text")


def _ollama(prompt, cfg):
    """IA locale gratuite (https://ollama.com)."""
    import socket
    import urllib.request
    with socket.create_connection(("127.0.0.1", 11434), timeout=3):
        pass  # Ollama non lancé → erreur immédiate → sélection heuristique
    body =json.dumps({"model": cfg.get("ollama_model", "qwen3:8b"), "stream": False,
                       "format": "json", "think": False, "keep_alive": "20s", "options": {"num_ctx": 12288, "temperature": 0.4},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["message"]["content"]


def _whole(sents, title, duration):
    first = sents[0]["t"] if sents else title
    return {"start": 0, "end": duration, "score": 5, "why": "source courte",
            "hook": " ".join(first.split()[:7]), "title": title[:60], "caption": title}


def _heuristic(sents, cfg, title):
    """Sans IA : fenêtres où l'on parle le plus densément."""
    mn, mx = cfg["min_seconds"], cfg["max_seconds"]
    wins = []
    for i, s in enumerate(sents):
        j, words = i, 0
        while j < len(sents) and sents[j]["e"] - s["s"] <= mx:
            words += len(sents[j]["t"].split())
            j += 1
        end = sents[j - 1]["e"]
        if end - s["s"] >= mn:
            wins.append((words / (end - s["s"]), s["s"], end, s["t"]))
    wins.sort(reverse=True)
    out = []
    for _, a, b, first in wins:
        if all(b <= c["start"] or a >= c["end"] for c in out):
            out.append({"start": a, "end": b, "score": 5, "why": "densité de parole",
                        "hook": " ".join(first.split()[:7]), "title": title[:60], "caption": title})
        if len(out) >= cfg["clips_per_video"]:
            break
    return out
