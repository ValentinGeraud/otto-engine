"""Choisit les meilleurs moments d'une transcription et écrit hook, titre et description."""
import json
import os
import re

PROMPT = """Tu es un monteur de Shorts/Reels/TikTok très exigeant. Niche du compte : {niche}.
À privilégier : {focus}

Transcription de la vidéo « {title} », découpée en phrases numérotées.
Format : #numéro [secondes] texte

{transcript}

Trouve au maximum {n} moments qui feraient un EXCELLENT Short. Un bon Short est une mini-histoire
COMPLÈTE qui se comprend sans avoir vu le reste :
  1. une mise en place claire (on comprend tout de suite de quoi on parle, ce qui se passe),
  2. une montée (tension, question, situation qui dérape, explication qui intrigue),
  3. une CHUTE : la punchline, le moment drôle, la réponse, le résultat, la réaction forte.
Le Short s'arrête juste APRÈS la chute (sur la réaction ou le rire qui suit), jamais avant.

Règles :
- "start_id" = numéro de la phrase où commence la mise en place (pas d'intro, pas de « euh »,
  pas de phrase qui renvoie à quelque chose d'avant : « comme je disais », « et du coup »).
- "end_id" = numéro de la phrase qui contient la chute ou la réaction qui la suit. L'histoire est
  TERMINÉE à cette phrase. Si tu hésites, prends une phrase plus loin plutôt qu'une plus tôt.
- Durée visée : {mn} à {mx} secondes (les [secondes] t'aident à estimer), jamais plus de {hard} s.
  Un moment drôle et complet de 15 s vaut mieux qu'un passage plat de 60 s.
- REFUSE les passages sans chute (discussion qui tourne en rond, consignes de jeu, bla-bla,
  remerciements, sponsors), ceux qui demandent du contexte, et ceux qu'on ne comprend qu'en voyant
  l'image quand le texte seul ne les rend ni drôles ni forts.
- Pas de chevauchement. Peu de moments mais excellents : si rien n'est vraiment bon, renvoie peu de clips.
- "score" de 1 à 10, SÉVÈRE : 9-10 = je le partage à mes potes, 7-8 = solide, 5 ou moins = à jeter.

Réponds UNIQUEMENT avec du JSON valide :
{{"clips": [{{"start_id": 12, "end_id": 19, "setup": "de quoi ça parle, en 1 phrase", "payoff": "la chute, en 1 phrase", "score": 8}}]}}"""


VERIFY = """Tu valides un Short avant publication. Niche : {niche}. À privilégier : {focus}
Vidéo source : « {title} ».

Extrait de la transcription autour du moment (phrases numérotées, [secondes]) :
{text}

Le Short proposé va de la phrase #{a} à la phrase #{b} ({dur:.0f} s).
Mise en place annoncée : {setup}
Chute annoncée : {payoff}

1. Quelqu'un qui ne voit QUE les phrases #{a} à #{b} comprend-il la situation dès le début ?
   Sinon, recule "start_id" (le moins possible) pour inclure la mise en place nécessaire.
2. La chute (punchline, réponse, résultat, réaction) est-elle DANS le passage, en entier ?
   Sinon, avance "end_id" jusqu'à la phrase qui la contient (ou la réaction juste après).
   Si le passage continue après la chute sur autre chose, recule "end_id" juste après la chute.
3. Durée finale idéale {mn} à {mx} s, jamais plus de {hard} s.
4. "keep" = false si, même ajusté, ce n'est pas un très bon Short (pas de chute, rien de drôle ou
   de fort, incompréhensible sans l'image ou sans le reste de la vidéo).
5. "score" de 1 à 10, sévère et honnête, pour le passage AJUSTÉ.

Puis écris pour ce Short, en {lang} correct et naturel, sans faute :
- "hook" : 4 à 9 mots affichés en haut de l'écran pendant {hook_s:.0f} secondes, qui donnent envie de
  voir la suite SANS dévoiler la chute. Il correspond à ce qui se passe VRAIMENT. Pas de point final,
  au maximum 1 emoji, pas de citation inventée.{hook_style}
- "title" : titre YouTube de 60 caractères maximum.{title_rule}
- "caption" : 1 phrase + 3 à 5 hashtags de NICHE (pas #fyp).
- "takeaway" : TON commentaire de 6 à 12 mots affiché à la fin : punchline ou avis tranché, pas un résumé.{question_rule}

Réponds UNIQUEMENT en JSON :
{{"start_id": {a}, "end_id": {b}, "keep": true, "score": 7, "why": "...", "hook": "...", "title": "...", "caption": "...", "takeaway": "...", "question": "..."}}"""


ACTION = """Tu valides un Short d'ACTION de jeu vidéo. Niche : {niche}. À privilégier : {focus}
Vidéo source : « {title} ».

Le son de la vidéo montre un pic net à {t:.0f} s (cris, réaction, explosion de but : +{db:.0f} dB au-dessus
du niveau habituel). Tu ne vois pas l'image : juge d'après ce qui se dit autour (vocal, commentateur).
Paroles entre {lo:.0f} s et {hi:.0f} s (format [secondes] texte) :
{text}

1. Est-ce une VRAIE belle action ou un moment drôle (geste technique, aérienne, arrêt fou, retournement,
   but de dernière seconde, clutch, fail hilarant) ? Un but ORDINAIRE n'en est pas un : chaque but fait du
   bruit dans le jeu. Il faut que les joueurs ou commentateurs RÉAGISSENT (« oh », « allez », « il ose »,
   « il est fou », cris, rires). S'ils parlent calmement ou d'autre chose (« bien tenté », « game de
   chauffe »), "keep" = false. "keep" = false aussi si le pic vient d'autre chose (musique, pub, bruit).
2. Bornes du Short en secondes : "start" = début de la construction de l'action (environ 8 à 15 s avant le
   pic), "end" = juste après la réaction (3 à 8 s après le pic). Durée totale 12 à 35 s, entre {lo:.0f} et {hi:.0f}.
3. "score" de 1 à 10, sévère.

Puis écris pour ce Short, en {lang} correct et naturel, sans faute :
- "hook" : 4 à 9 mots affichés en haut de l'écran pendant {hook_s:.0f} secondes, qui annoncent l'action
  SANS la dévoiler (« Il tente l'impossible en fin de match 😳 »). Pas de point final, 1 emoji max.{hook_style}
- "title" : titre YouTube de 60 caractères maximum.{title_rule}
- "caption" : 1 phrase + 3 à 5 hashtags de NICHE (pas #fyp).
- "takeaway" : TON commentaire de 6 à 12 mots affiché à la fin : punchline ou avis tranché.{question_rule}

Réponds UNIQUEMENT en JSON :
{{"keep": true, "start": {s0:.0f}, "end": {e0:.0f}, "score": 7, "why": "...", "hook": "...", "title": "...", "caption": "...", "takeaway": "...", "question": "..."}}"""


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


def pick(sents, cfg, title, duration, creator="", keep=None, video=None):
    clips = _pick(sents, cfg, title, duration, creator, keep, video)
    return [finish(c, cfg, creator, title) for c in clips]


def _numbered(sents, a, b):
    return "\n".join(f"#{k} [{sents[k]['s']:.0f}] {sents[k]['t']}" for k in range(a, b + 1))


def _chunks(sents, size=900, overlap=90, max_chars=None):
    """Tranches de ~15 min qui se chevauchent de 1 min 30 : une histoire à cheval sur deux tranches
    est vue en entier dans l'une des deux. Renvoie des couples (premier, dernier) d'indices.
    max_chars : plafond de texte par tranche (l'offre gratuite de Groq refuse une demande de plus de
    ~8 000 jetons par minute : une vidéo très bavarde est donc découpée plus finement)."""
    if max_chars is None:
        max_chars = int(os.environ.get("CHUNK_MAX_CHARS", "13000" if os.environ.get("OTTO_AI") == "groq" else "60000"))
    out, i = [], 0
    while i < len(sents):
        j, chars = i, len(sents[i]["t"]) + 12
        while j + 1 < len(sents) and sents[j + 1]["e"] - sents[i]["s"] <= size \
                and chars + len(sents[j + 1]["t"]) + 12 <= max_chars:
            j += 1
            chars += len(sents[j]["t"]) + 12
        out.append((i, j))
        if j + 1 >= len(sents):
            break
        k = j
        while k > i and sents[j]["e"] - sents[k]["s"] < overlap:
            k -= 1
        i = max(k, i + 1)
    return out


def _ids(c, lo, hi):
    """Numéros de phrases renvoyés par l'IA, s'ils sont valides dans [lo, hi]."""
    try:
        a, b = int(c.get("start_id")), int(c.get("end_id"))
    except (TypeError, ValueError):
        return None
    return (a, b) if lo <= a <= b <= hi else None


def _complete(sents, b):
    """Si la dernière phrase est coupée net (pas de ponctuation, enchaînement immédiat), on prend la
    suivante : la transcription coupe parfois une phrase en deux."""
    for _ in range(2):
        if b + 1 < len(sents) and not sents[b]["t"].rstrip().endswith((".", "!", "?", "…")) \
                and sents[b + 1]["s"] - sents[b]["e"] < 0.6:
            b += 1
    return b


# Passages sponsorisés : l'IA les laisse parfois passer, on les écarte à coup sûr.
SPONSOR = re.compile(
    r"sponsor|partenaire de (cette|la) vid[ée]o|code promo|lien (en|dans la) description|"
    r"profite[rz]? de l'offre|ouvr(ez|ir|ant) un compte|"
    r"promo code|link in (the )?description|this video is sponsored|thanks to .{0,30} for sponsoring|"
    r"use (my )?code|patrocin|c[oó]digo (de descuento|promocional)|enlace en la descripci[oó]n", re.I)


def _is_sponsor(sents, a, b):
    return bool(SPONSOR.search(" ".join(s["t"] for s in sents[a: b + 1])))


def _setup_back(sents, a, b, floor):
    """Moment trop court (souvent la chute seule) : on remonte, phrase par phrase, vers sa mise en place,
    tant que les phrases s'enchaînent (pas de vraie pause) et sans dépasser 4 phrases."""
    k = 0
    while a > 0 and k < 4 and sents[b]["e"] - sents[a]["s"] < floor and sents[a]["s"] - sents[a - 1]["e"] < 1.5:
        a -= 1
        k += 1
    return a


def _verify(c, sents, cfg, title, creator, ask, mn, mx, hard):
    """2e passe : relit le moment avec un peu de contexte, ajuste début/fin pour avoir la mise en place
    ET la chute, le rejette s'il ne tient pas debout seul, et écrit hook / titre / textes."""
    a, b = c["_a"], c["_b"]
    lo, hi = max(0, a - 8), min(len(sents) - 1, b + 8)
    prompt = VERIFY.format(niche=cfg["niche"], focus=cfg.get("focus", ""), title=title,
                           text=_numbered(sents, lo, hi)[:9000], a=a, b=b,
                           dur=sents[b]["e"] - sents[a]["s"], setup=c.get("setup") or "?",
                           payoff=c.get("payoff") or "?", mn=mn, mx=mx, hard=hard, lang=_lang(cfg),
                           **_hook_args(cfg), **_text_args(cfg, creator))
    d = json.loads(re.search(r"\{.*\}", ask(prompt, cfg), re.S).group(0))
    a, b = _ids(d, lo, hi) or (a, b)
    b = _complete(sents, b)
    a = _setup_back(sents, a, b, max(12, mn * 0.5))
    if _is_sponsor(sents, a, b):
        d["keep"] = False
        c["payoff"] = "passage sponsorisé"
    c["_a"], c["_b"] = a, b
    c["start"], c["end"] = round(sents[a]["s"], 2), round(sents[b]["e"], 2)
    for k in ("hook", "title", "caption", "takeaway", "question", "why"):
        if d.get(k):
            c[k] = d[k]
    if isinstance(d.get("score"), (int, float)):
        c["score"] = d["score"]
    c["keep"] = d.get("keep") is not False
    return c


# Réactions qui font une belle action : sans elles, un pic de son n'est souvent qu'un but banal (validé
# sur les choix du propriétaire : 7 sur 8 bien triés).
REACT = re.compile(
    r"\b(oh+|o+h là|ola+|wow+|waouh|allez+|quoi+ ?\?|incroyable|de ouf|il est fou|t'es fou|c'est fou|il ose|"
    r"mais non|non non|putain|purée|la vache|énorme|trop fort|monstre|what+|let'?s go|no way|oh my god|omg|"
    r"insane|holy|crazy|vamos|qu[eé] locura|madre m[ií]a|incre[ií]ble|haha+|mdr|ptdr|\[rires?\]|\[laugh\w*\])",
    re.I)


def _reaction(sents, t, a=-4, b=7):
    txt = " ".join(s["t"] for s in sents if s["e"] >= t + a and s["s"] <= t + b)
    return txt.count("!") + 1.5 * len(REACT.findall(txt))


def _action_cands(video, sents, cfg, duration):
    """Candidats « action » repérés au son (jeux vidéo) : pic de volume = but, arrêt, cri, réaction.
    Gardés seulement si les joueurs ou commentateurs réagissent vraiment juste après (min_reaction)."""
    import actions
    if not video or not actions.wanted(cfg):
        return []
    try:
        found = actions.peaks(video, max_peaks=30)
    except Exception as e:
        print(f"  (analyse du son impossible : {str(e)[:60]})")
        return []
    end_max = duration or (sents[-1]["e"] if sents else 0)
    out = []
    need = cfg.get("min_reaction", 2)
    quiet = 0
    for t, db in found:
        r = _reaction(sents, t)
        if r < need:   # personne ne réagit : but ou bruit ordinaire
            quiet += 1
            continue
        out.append({"kind": "action", "t": t, "db": db, "start": max(0.0, t - 12), "end": min(end_max, t + 6),
                    "score": min(9, 5 + r / 2)})
    out = out[:12]
    if quiet:
        print(f"  {quiet} pic(s) de son sans vraie réaction écarté(s) (but ou bruit ordinaire)")
    if out:
        print(f"  {len(out)} pic(s) de son repéré(s) (actions possibles) : "
              + ", ".join(f"{c['t']:.0f}s" for c in out[:8]))
    return out


def _ask_vision(prompt, images, cfg):
    """Même demande, avec des images : Groq (modèle qui voit) dans le cloud, Ollama (gemma3) sur le PC.
    Sans modèle qui voit disponible, on retombe sur le texte seul."""
    import urllib.request
    if os.environ.get("OTTO_AI") == "groq" and os.environ.get("GROQ_API_KEY"):
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in images]
        return _groq(prompt, cfg, content=content, kind="vision")
    body = json.dumps({"model": cfg.get("ollama_vision_model", "gemma3:4b"), "stream": False, "format": "json",
                       "keep_alive": "20s", "options": {"num_ctx": 8192, "temperature": 0.3},
                       "messages": [{"role": "user", "content": prompt, "images": images}]}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["message"]["content"]


VISION_NOTE = """
Tu vois aussi {n} images de la vidéo : {when}. Sers-t'en pour savoir ce qui s'est VRAIMENT passé
(but marqué et par qui : « X SCORED! », arrêt, action ratée, score affiché…) : le hook et le titre
doivent décrire l'action réelle, sans la dévoiler.
"""


def _verify_action(c, sents, cfg, title, creator, ask, duration, video=None):
    t = c["t"]
    lo, hi = max(0.0, t - 40), t + 20
    if duration:
        hi = min(hi, duration)
    text = "\n".join(f"[{s['s']:.0f}] {s['t']}" for s in sents if s["e"] >= lo and s["s"] <= hi) or "(personne ne parle)"
    prompt = ACTION.format(niche=cfg["niche"], focus=cfg.get("focus", ""), title=title, t=t, db=c["db"],
                           lo=lo, hi=hi, text=text[:6000], s0=c["start"], e0=c["end"], lang=_lang(cfg),
                           **_hook_args(cfg), **_text_args(cfg, creator))
    reply = None
    if video:
        import actions
        times = [t - 8, t - 2, t + 1]
        imgs = actions.frames(video, times)
        if imgs:
            when = ", ".join(f"{x:.0f} s" for x in times[: len(imgs)])
            try:
                reply = _ask_vision(prompt + VISION_NOTE.format(n=len(imgs), when=when), imgs, cfg)
            except Exception as e:
                print(f"  (images non analysées : {str(e)[:60]})")
    d = json.loads(re.search(r"\{.*\}", reply or ask(prompt, cfg), re.S).group(0))
    try:
        a, b = float(d.get("start")), float(d.get("end"))
        # l'action doit contenir le pic, et rester raisonnable (12 à 40 s)
        if lo <= a < t <= b <= hi + 1 and 10 <= b - a <= 40:
            c["start"], c["end"] = round(a, 2), round(b, 2)
    except (TypeError, ValueError):
        pass
    for k in ("hook", "title", "caption", "takeaway", "question", "why"):
        if d.get(k):
            c[k] = d[k]
    if isinstance(d.get("score"), (int, float)):
        c["score"] = d["score"]
    c["keep"] = d.get("keep") is not False
    c["payoff"] = f"action au son à {t:.0f}s"
    return c


def _pick(sents, cfg, title, duration, creator="", keep=None, video=None):
    mn, mx = cfg["min_seconds"], cfg["max_seconds"]
    hard = cfg.get("hard_max_seconds") or max(mx, 60)
    want = cfg["clips_per_video"]
    min_score = cfg.get("min_score", 6)
    # Source déjà courte (clip Twitch...) : on la garde en entier
    if duration and duration <= mx + 2:
        c = _whole(sents, title, duration)
        if ai_ok(cfg):  # un clip Twitch a souvent un titre vide (« . », « 1 ») : l'IA réécrit
            _polish(c, sents, cfg, title, creator)
        return [c]
    ai = cfg.get("ai", "ollama")
    if ai == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        ask, chunks = _claude, [(0, len(sents) - 1)]
    elif ai == "ollama":  # Ollama sur le PC, ou Groq dans le cloud (OTTO_AI=groq)
        ask, chunks = _ollama, _chunks(sents)
    else:
        return _heuristic(sents, cfg, title)
    per_chunk = max(2, min(5, want)) if len(chunks) == 1 else 3

    # 1re passe : repérage des mini-histoires (début = mise en place, fin = chute), par numéro de phrase
    # (l'IA ne donne plus de minutage : elle inventait des fins « début + 20 s » au milieu de l'histoire)
    cands, failed = [], 0
    for n, (lo, hi) in enumerate(chunks):
        prompt = PROMPT.format(niche=cfg["niche"], focus=cfg.get("focus", ""), title=title,
                               transcript=_numbered(sents, lo, hi)[:300_000], n=per_chunk,
                               mn=mn, mx=mx, hard=hard)
        try:
            data = json.loads(re.search(r"\{.*\}", ask(prompt, cfg), re.S).group(0))
            for c in data.get("clips", []):
                ids = _ids(c, lo, hi)
                if not ids:
                    continue
                c["_a"], c["_b"] = ids
                c["score"] = c["score"] if isinstance(c.get("score"), (int, float)) else 5
                cands.append(c)
        except Exception as e:
            failed += 1
            print(f"  (tranche {n + 1}/{len(chunks)} illisible : {str(e)[:60]})")
        if len(chunks) > 1:
            print(f"  … tranche {n + 1}/{len(chunks)} analysée ({len(cands)} moments repérés)")
    for c in cands:
        c["start"], c["end"] = sents[c["_a"]]["s"], sents[c["_b"]]["e"]
    # jeux vidéo : belles actions repérées au son (but, arrêt, cri), même quand personne ne raconte rien
    acts = _action_cands(video, sents, cfg, duration)
    if not cands and not acts:
        if failed == len(chunks):
            print("  (IA indisponible → sélection heuristique)")
            return _heuristic(sents, cfg, title)
        print("  (aucun moment assez fort dans cette vidéo)")
        return []

    # doublons (tranches qui se chevauchent, action déjà dans une histoire) : on garde le mieux noté
    pool = sorted(cands + acts, key=lambda c: c["score"], reverse=True)
    uniq = []
    for c in pool:
        if all(c["end"] <= u["start"] or c["start"] >= u["end"] for u in uniq):
            uniq.append(c)
    # avec des actions, on en vérifie au moins quelques-unes même si les histoires sont mieux notées
    if acts:
        first = [c for c in uniq if c.get("kind") == "action"][: max(2, want // 2)]
        uniq = first + [c for c in uniq if c not in first]

    # 2e passe : vérification une par une (les mieux notées d'abord), jusqu'à en avoir assez
    picked = []
    for c in uniq[: want * 2 + 4]:
        if len(picked) >= want:
            break
        try:
            if c.get("kind") == "action":
                _verify_action(c, sents, cfg, title, creator, ask, duration, video)
            else:
                _verify(c, sents, cfg, title, creator, ask, mn, mx, hard)
        except Exception as e:
            print(f"  (vérification impossible : {str(e)[:60]})")
            c["keep"] = c.get("kind") != "action"
        c["start"], c["end"] = round(c["start"], 2), round(c["end"], 2)
        dur = c["end"] - c["start"]
        overlap = any(not (c["end"] <= p["start"] or c["start"] >= p["end"]) for p in picked)
        verdict = ("trop long" if dur > hard + 5 else "trop court" if dur < 8 else
                   "rejeté à la relecture" if not c["keep"] else
                   f"note {c['score']} < {min_score}" if c["score"] < min_score else
                   "chevauche un autre clip" if overlap else "")
        label = "action" if c.get("kind") == "action" else f"#{c['_a']}→#{c['_b']}"
        print(f"  {label} {c['start']:.0f}s→{c['end']:.0f}s ({dur:.0f}s) note {c['score']}"
              f" · {str(c.get('payoff') or '')[:70]}{' → ' + verdict if verdict else ''}", flush=True)
        if verdict:
            continue
        if keep and not keep(c):  # filtre imposé (ex. personne visible à l'image)
            continue
        picked.append(c)
    for c in picked:
        if c.get("kind") == "action":
            c["action"] = True   # le montage ne coupera pas les silences de ce clip
        for k in ("_a", "_b", "keep", "setup", "payoff", "kind", "t", "db"):
            c.pop(k, None)
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


# Groq retire régulièrement des modèles : on ne dépend d'aucun nom. Modèle introuvable (404) ou quota du jour
# épuisé → on lit la liste des modèles disponibles et on prend le meilleur suivant, dans cet ordre de préférence.
GROQ_PREFS = {
    "text": [r"gpt-oss-120b", r"qwen3\.\d+-\d+b", r"llama-3\.3-70b", r"kimi-k2", r"deepseek",
             r"gpt-oss-20b", r"qwen", r"llama"],
    # 11/2026 : seul qwen/qwen3.8-27b lit les images sur Groq (llama-4 scout/maverick retirés)
    "vision": [r"qwen3\.\d+-\d+b", r"vision", r"llama-4", r"muse"],
}
GROQ_DEFAULT = {"text": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
                "vision": os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")}
_GROQ_STATE = {"excluded": set()}


def _groq_models():
    import urllib.request
    if "models" not in _GROQ_STATE:
        req = urllib.request.Request("https://api.groq.com/openai/v1/models", headers={
            "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "User-Agent": "otto-engine"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read()).get("data", [])
        _GROQ_STATE["models"] = [m["id"] for m in data if m.get("active", True)
                                 and not re.search(r"whisper|tts|guard|playai|orpheus|compound|prompt", m["id"], re.I)]
        print(f"  (modèles Groq disponibles : {', '.join(_GROQ_STATE['models'][:12])})", flush=True)
    return _GROQ_STATE["models"]


def _groq_pick(kind):
    ids = [i for i in _groq_models() if i not in _GROQ_STATE["excluded"]]
    for pat in GROQ_PREFS[kind]:
        for i in ids:
            if re.search(pat, i, re.I):
                return i
    return ids[0] if ids and kind == "text" else None


def _groq(prompt, cfg, content=None, kind="text"):
    """IA gratuite dans le cloud (https://console.groq.com, API compatible OpenAI), utilisée quand
    le moteur tourne sans carte graphique (GitHub Actions) : OTTO_AI=groq + GROQ_API_KEY.
    Offre gratuite limitée en jetons par minute : on attend et on réessaie sur une erreur 429."""
    import time
    import urllib.error
    import urllib.request
    for attempt in range(8):
        use = _GROQ_STATE.get(kind) or GROQ_DEFAULT[kind]
        payload = {"model": use, "temperature": 0.4, "response_format": {"type": "json_object"},
                   # la réponse réservée compte dans la limite de jetons par minute : on la plafonne
                   "max_tokens": 2500, "messages": [{"role": "user", "content": content or prompt}]}
        if use.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = "low"   # réflexion courte : la réponse JSON tient dans max_tokens
        body = json.dumps(payload).encode()
        req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
            "User-Agent": "otto-engine"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            gone = e.code in (400, 404) and re.search(r"model", detail, re.I) and \
                re.search(r"not found|does not exist|decommission|not supported|no longer", detail, re.I)
            daily = e.code == 429 and re.search(r"per day|TPD|RPD", detail)
            if gone or daily:
                _GROQ_STATE["excluded"].add(use)
                nxt = _groq_pick(kind)
                if not nxt:
                    raise RuntimeError(f"Groq : aucun modèle {kind} disponible ({detail[:120]})")
                print(f"  ({use} {'retiré par Groq' if gone else 'a épuisé son quota du jour'} → {nxt})", flush=True)
                _GROQ_STATE[kind] = nxt
                continue
            if e.code not in (429, 500, 502, 503) or attempt == 7:
                raise RuntimeError(f"Groq HTTP {e.code} : {detail[:200]}")
            wait = float(e.headers.get("retry-after") or 0) or 15 * (attempt + 1)
            print(f"  (Groq occupé, nouvel essai dans {wait:.0f} s)", flush=True)
            time.sleep(min(wait, 90))


def _ollama(prompt, cfg):
    """IA locale gratuite (https://ollama.com). Dans le cloud (OTTO_AI=groq), la même demande part chez Groq."""
    if os.environ.get("OTTO_AI") == "groq" and os.environ.get("GROQ_API_KEY"):
        return _groq(prompt, cfg)
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
