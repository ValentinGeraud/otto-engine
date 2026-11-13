"""Connexion unique à YouTube : choisis la bonne chaîne dans la fenêtre Google.

  python youtube_auth.py                        chaîne principale (Clip by Cut)
  python youtube_auth.py --channel en_business  chaîne secondaire (channels/<id>/config.yaml)

Si publish.youtube_channel vaut encore REMPLACE_MOI, la chaîne choisie dans la fenêtre Google
est enregistrée dans channels/<id>/config.yaml (nom + identifiant UC...), ce qui active
la publication.
"""
import argparse
import re
import sys

import channels
import publish

sys.stdout.reconfigure(encoding="utf-8")
ap = argparse.ArgumentParser()
ap.add_argument("--channel", help=f"chaîne secondaire : {', '.join(channels.available())}")
cfg = channels.load(ap.parse_args().channel)
publish.configure(cfg)
expected = cfg["publish"].get("youtube_channel", "")

if cfg["_id"] and not cfg["publish"].get("youtube_channel_id"):
    # première connexion : on vérifie le nom, puis on fige l'identifiant UC... dans la config
    try:
        ch = publish._youtube().channels().list(part="snippet", mine=True).execute()["items"][0]
    except RuntimeError as e:
        sys.exit(str(e))
    name, cid = ch["snippet"]["title"], ch["id"]
    # garde-fou : une chaîne déjà utilisée par une autre config ne peut pas être reprise
    others = [channels.load(None)] + [channels.load(c) for c in channels.available() if c != cfg["_id"]]
    for o in others:
        op = o["publish"]
        if cid == op.get("youtube_channel_id") or name.strip().lower() == \
                str(op.get("youtube_channel", "")).strip().lower():
            publish.TOKEN.unlink(missing_ok=True)
            sys.exit(f"Refusé, accès supprimé : « {name} » est déjà la chaîne de "
                     f"{o['_id'] or 'la chaîne principale'}. Relance et choisis la bonne chaîne.")
    if "REMPLACE" not in expected and name.strip().lower() != expected.strip().lower():
        publish.TOKEN.unlink(missing_ok=True)
        sys.exit(f"Mauvaise chaîne, accès supprimé : connecté à « {name} » au lieu de "
                 f"« {expected} ». Relance et choisis la bonne chaîne (ou corrige le nom).")
    f = channels.DIR / cfg["_id"] / "config.yaml"
    text = f.read_text(encoding="utf-8")
    line = (f'  youtube_channel: "{name}"\n'
            f'  youtube_channel_id: "{cid}"   # vérifié avant chaque envoi')
    text = re.sub(r'^  youtube_channel: .*$', lambda _: line, text, count=1, flags=re.M)
    f.write_text(text, encoding="utf-8")
    print(f"Connecté à la chaîne : {name} ({cid}) ✓")
    print(f"Enregistrée dans channels/{cfg['_id']}/config.yaml : la publication est activée.")
    sys.exit()

try:
    print(f"Connecté à la chaîne : {publish.check_channel(expected)} ✓")
except RuntimeError as e:
    publish.TOKEN.unlink(missing_ok=True)
    print(f"Mauvaise chaîne, accès supprimé : {e}")
