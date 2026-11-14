"""Écrit les cookies YouTube (secret YT_COOKIES) dans un fichier au format Netscape pour yt-dlp.

Accepte ce que donnent les extensions et le copier-coller : format Netscape (avec retours à la ligne
Windows, BOM, ou tabulations devenues des espaces) ou export JSON. N'affiche jamais les valeurs.

  python ytcookies.py <fichier de sortie>     # lit la variable d'environnement YT_COOKIES
"""
import json
import os
import sys


def to_netscape(raw: str):
    raw = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").strip()
    rows = []
    if raw[:1] in "[{":
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("cookies") or [data]
        for c in data:
            domain = c.get("domain") or c.get("host") or ""
            exp = c.get("expirationDate") or c.get("expires") or c.get("expiry") or 0
            rows.append([domain, "TRUE" if domain.startswith(".") else "FALSE", c.get("path") or "/",
                         "TRUE" if c.get("secure") else "FALSE", str(int(float(exp or 0))),
                         c.get("name") or "", str(c.get("value") or "")])
        fmt = "json"
    else:
        for line in raw.split("\n"):
            line = line.strip()
            if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            parts = line.split("\t")
            if len(parts) < 7:  # tabulations perdues au copier-coller : 6 champs puis la valeur
                parts = line.split(None, 6)
            if len(parts) >= 7:
                rows.append(parts[:6] + ["\t".join(parts[6:])])
        fmt = "netscape"
    rows = [r for r in rows if r[5] and ("youtube" in r[0] or "google" in r[0])]
    out = "# Netscape HTTP Cookie File\n" + "".join("\t".join(r) + "\n" for r in rows)
    return out, fmt, rows


if __name__ == "__main__":
    raw = os.environ.get("YT_COOKIES", "")
    if not raw.strip():
        sys.exit("YT_COOKIES vide")
    text, fmt, rows = to_netscape(raw)
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    domains = sorted({r[0].lstrip(".").replace("#HttpOnly_", "") for r in rows})
    print(f"cookies : format {fmt}, {len(rows)} cookie(s) pour {', '.join(domains) or 'aucun domaine'}")
    if not rows:
        sys.exit("aucun cookie YouTube/Google trouvé dans YT_COOKIES")
