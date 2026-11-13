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
- Chaque clip dure idéalement entre {mn} et {mx} secondes. La durée suit le MOMENT : si
  l'explication, l'histoire ou la blague a besoin de plus de temps, va jusqu'à {hard} secondes.
  Ne coupe JAMAIS au milieu d'une explication, d'une énumération ou avant la chute.
- Il commence EXACTEMENT sur une phrase forte (pas d'intro, pas de « euh », pas de contexte),
  et finit quand l'idée est TERMINÉE : conclusion, chute ou réponse donnée, phrase complète.
- Il doit se comprendre sans avoir vu le reste de la vidéo.
- Pas de chevauchement entre les clips.
- Note la viralité de chaque clip de 1 à 10, honnêtement.

Pour chaque clip, écris :
- "hook" : texte affiché en haut de l'écran pendant les {hook_s:.0f} premières secondes, 4 à 9 mots,
  percutant, qui crée une curiosité. Pas de point final, au maximum 1 emoji.{hook_style}
- "title" : titre YouTube Short de 60 caractères maximum.{title_rule}
- "caption" : description courte (1 phrase) suivie de 3 à 5 hashtags de NICHE (pas #fyp).
- "takeaway" : TON commentaire en 6 à 12 mots, affiché à la fin du clip : une punchline,
  un avis tranché ou une leçon. Il doit apporter quelque chose, pas résumer ce qu'on vient de voir.{question_rule}
IMPORTANT : hook, title, caption, takeaway et question sont écrits en {lang}, la langue du public de la chaîne.

Réponds UNIQUEMENT avec du JSON valide :
{{"clips": [{{"start": 12.3, "end": 48.9, "score": 8, "why": "...", "hook": "...", "title": "...", "caption": "...", "takeaway": "...", "question": "..."}}]}}"""


LANGS = {"fr": "français", "en": "anglais (natural American English)",
         "es": "espagnol (español neutro, compréhensible en Espagne et en Amérique latine)"}


def _lang(cfg):
    return LANGS.get(cfg.get("language", "fr"), cfg.get("language", "fr"))


def _hook_args(cfg):
    """Durée d'affichage et ton du hook, propres à chaque chaîne."""
    tone = " ".join((cfg.get("hook_style") or "").split())
    return {"hook_s": cfg.get("style", {}).get("hook_seconds", 3.0),
            "hook_style": f"\n  Ton du hook : {tone}" if tone else ""}


def _text_args(cfg, creator):
    """Règles en plus pour le titre (créateur nommé) et la question finale (commentaires)."""
    title_rule = question_rule = ""
    if cfg.get("creator_in_title", True) and creator:
        title_rule = (f"\n  Le titre NOMME la personne qu'on voit : le créateur ({creator}) ou un invité"
                      f" connu cité dans le titre de la vidéo, au début du titre, avec le ton du hook"
                      f" (ex. « Michou perdu en pleine nuit 💀 », pas « Undercover Nocturne : Perdus"
                      f" dans la nuit »). Pas de « : » ni de nom d'émission ou de concept de la vidéo.")
    if cfg.get("comment_question", True):
        question_rule = ("\n- \"question\" : une question courte (4 à 10 mots) posée au spectateur, affichée"
                         " à l'écran sous le takeaway (pas lue), qui donne envie de répondre en commentaire : un choix"
                         " entre deux camps ou un avis (ex. « Team tartine ou team jambon ? »,"
                         " « Toi, tu aurais tenu combien de temps ? »). Elle finit par « ? ».")
    return {"title_rule": title_rule, "question_rule": question_rule}


def finish(c, cfg, creator="", source_title=""):
    """Après l'IA : garantit le créateur dans le titre et nettoie la question finale."""
    title = " ".join(str(c.get("title") or "").split())
    if cfg.get("creator_in_title", True) and creator:
        # le créateur, ou un invité nommé dans le titre de la vidéo (« ft Michou », « Avec Maghla »)
        guests = [w for w in re.findall(r"[^\W\d_][\w'-]{3,}", source_title)
                  if w[0].isupper() and not w.isupper()
                  and w.lower() not in {"avec", "feat", "with", "con", "pour", "dans"}]
        names = [creator] + [w for w in re.split(r"[\s,()]+", creator) if len(w) > 3] + guests
        if title and not any(n.lower() in title.lower() for n in names):
            title = f"{creator} – {title}"
    c["title"] = title[:100]
    # la question reste À PART : affichée sous le verdict et dans la description, jamais lue
    q = " ".join(str(c.get("question") or "").split())
    if cfg.get("comment_question", True) and q:
        c["question"] = q if q.endswith("?") else q.rstrip(" .!") + " ?"
    else:
        c.pop("question", None)
    return c


# Mots qui annoncent que la phrase suivante CONTINUE l'idée (explication, liste, chute)
CONTINUE = {
    "fr": ("parce", "car", "donc", "mais", "et", "alors", "puis", "du coup", "en fait", "sauf",
           "par contre", "c'est-à-dire", "genre", "sinon", "ensuite", "après", "deuxième",
           "troisième", "et puis", "bref", "résultat", "pourquoi", "comme"),
    "en": ("because", "so", "but", "and", "then", "which", "that's why", "and then", "or",
           "plus", "also", "second", "third", "finally", "until", "unless", "cause", "'cause",
           "except", "like", "basically", "which means", "otherwise", "after"),
    "es": ("porque", "pero", "y", "entonces", "luego", "así que", "o sea", "pues", "además",
           "después", "segundo", "tercero", "hasta", "aunque", "sino", "como"),
}


def _lang_code(cfg):
    return (cfg.get("language") or "fr")[:2]


def _continues(prev, nxt, lang):
    """La phrase `nxt` prolonge-t-elle l'idée de `prev` ?"""
    if nxt["s"] - prev["e"] > 1.2:          # vraie pause : l'idée est probablement finie
        return False
    if not prev["t"].rstrip().endswith((".", "!", "?", "…")):
        return True                          # phrase coupée net : la suite est nécessaire
    if prev["t"].rstrip().endswith(":") or prev["t"].rstrip().endswith(","):
        return True
    head = nxt["t"].lower().lstrip(" «\"'-")
    return any(head == w or head.startswith(w + " ") or head.startswith(w + ",")
               for w in CONTINUE.get(lang, CONTINUE["en"]))


def snap(c, sents, mn, mx, hard, lang="fr"):
    """Recale un moment sur les phrases de la transcription :
    - début = début de la phrase où il tombe ;
    - fin = fin de la phrase où il tombe, puis on PROLONGE tant que la phrase suivante continue
      l'explication (« parce que… », « et du coup… », phrase sans point final), jusqu'à `hard` s ;
    - trop court : on ajoute des phrases entières jusqu'à `mn` s ;
    - trop long : on retire des phrases ENTIÈRES par la fin (jamais de coupe au milieu d'un mot)."""
    if not sents:
        return c
    i = max((k for k, s in enumerate(sents) if s["s"] <= c["start"] + 0.3), default=0)
    if sents[i]["e"] < c["start"] and i + 1 < len(sents):   # tombé dans un blanc
        i += 1
    j = max((k for k, s in enumerate(sents) if s["s"] < c["end"] - 0.3), default=i)
    j = max(j, i)
    start = sents[i]["s"]
    while j + 1 < len(sents) and _continues(sents[j], sents[j + 1], lang)             and sents[j + 1]["e"] - start <= hard:
        j += 1
    while j + 1 < len(sents) and sents[j]["e"] - start < mn and sents[j + 1]["e"] - start <= hard:
        j += 1
    while j > i and sents[j]["e"] - start > hard:
        j -= 1
    end = sents[j]["e"]
    if end - start > hard:   # une seule « phrase » interminable (transcription sans ponctuation)
        end = start + hard
    c["start"], c["end"] = round(start, 2), round(end, 2)
    return c


def _fmt(sents):
    return "\n".join(f"[{s['s']:.1f}-{s['e']:.1f}] {s['t']}" for s in sents)


def pick(sents, cfg, title, duration, creator="", keep=None):
    clips = _pick(sents, cfg, title, duration, creator, keep)
    return [finish(c, cfg, creator, title) for c in clips]


def _pick(sents, cfg, title, duration, creator="", keep=None):
    mn, mx = cfg["min_seconds"], cfg["max_seconds"]
    hard = cfg.get("hard_max_seconds", mx)
    # Source déjà courte (clip Twitch...) : on la garde en entier
    if duration and duration <= mx + 2:
        c = _whole(sents, title, duration)
        if ai_ok(cfg):  # un clip Twitch a souvent un titre vide (« . », « 1 ») : l'IA réécrit
            _polish(c, sents, cfg, title, creator)
        return [c]
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
                               **_hook_args(cfg), **_text_args(cfg, creator),
                               lang=_lang(cfg),
                               transcript=_fmt(ch)[:300_000], n=per_chunk, mn=mn, mx=mx, hard=hard)
        try:
            data = json.loads(re.search(r"\{.*\}", ask(prompt, cfg), re.S).group(0))
            got = [c for c in data.get("clips", [])
                   if isinstance(c.get("start"), (int, float)) and isinstance(c.get("end"), (int, float))
                   and ch[0]["s"] - 5 <= c["start"] < c["end"] <= ch[-1]["e"] + 60]
            # bornes recalées sur les phrases : début de phrase, fin d'idée (jamais en plein milieu)
            for c in got:
                snap(c, sents, mn, mx, hard, _lang_code(cfg))
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
    if keep:  # filtre imposé (ex. personne visible à l'image), avant la 2e passe coûteuse
        ok = []
        for c in picked:
            if len(ok) >= cfg["clips_per_video"]:
                break
            if keep(c):
                ok.append(c)
        picked = ok
    if ask is _ollama:  # 2e passe : textes réécrits en ne lisant QUE le passage choisi
        for c in picked[: cfg["clips_per_video"]]:
            _polish(c, sents, cfg, title, creator)
    return picked


POLISH = """Voici l'extrait exact d'une vidéo YouTube « {title} » (de {a:.0f}s à {b:.0f}s) :

{text}

Écris pour ce Short, en {lang} correct et naturel, sans faute :
- "hook" : 4 à 9 mots affichés en haut de l'écran pendant {hook_s:.0f} secondes, qui donnent envie
  de regarder la suite. Il doit correspondre à ce qui se passe VRAIMENT dans l'extrait.
  Pas de citation inventée.{hook_style}
- "title" : titre YouTube de 60 caractères maximum.{title_rule}
- "caption" : 1 phrase + 3 hashtags pertinents.
- "takeaway" : un commentaire drôle ou tranché de 6 à 12 mots sur ce moment.{question_rule}
- "score" : de 1 à 10, est-ce que ce moment est vraiment captivant sans contexte ?

Réponds UNIQUEMENT en JSON : {{"hook": "...", "title": "...", "caption": "...", "takeaway": "...", "question": "...", "score": 7}}"""


def _polish(c, sents, cfg, title, creator=""):
    text = " ".join(s["t"] for s in sents if s["e"] > c["start"] and s["s"] < c["end"])
    try:
        d = json.loads(re.search(r"\{.*\}", _ollama(POLISH.format(
            title=title, a=c["start"], b=c["end"], text=text[:4000], lang=_lang(cfg),
            **_hook_args(cfg), **_text_args(cfg, creator)), cfg),
            re.S).group(0))
        for k in ("hook", "title", "caption", "takeaway", "question", "score"):
            if d.get(k):
                c[k] = d[k]
    except Exception as e:
        print(f"  (réécriture impossible : {str(e)[:60]})")


def ai_ok(cfg):
    """Une IA est-elle utilisable pour réécrire les textes ?"""
    return cfg.get("ai", "ollama") == "ollama"


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
