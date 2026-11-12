"""Plusieurs chaînes YouTube avec le même pipeline.

- Chaîne principale (Clip by Cut) : config.yaml, state.json, output/ à la racine de clipper/.
- Autres chaînes : channels/<id>/config.yaml ne contient QUE ce qui change par rapport à
  config.yaml (sources, langue, niche, DA...). Chaque chaîne a son propre state.json,
  output/, a_poster/, analytics.json et run.log dans channels/<id>/, et son propre jeton
  YouTube dans secrets/youtube_token_<id>.json.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
DIR = ROOT / "channels"


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def available():
    return sorted(p.parent.name for p in DIR.glob("*/config.yaml"))


def load(channel=None, config_path=None):
    """Config fusionnée + chemins propres à la chaîne (clés commençant par `_`)."""
    cfg = yaml.safe_load(Path(config_path or ROOT / "config.yaml").read_text(encoding="utf-8"))
    base = ROOT
    if channel:
        f = DIR / channel / "config.yaml"
        if not f.exists():
            raise SystemExit(f"Chaîne inconnue « {channel} ». Disponibles : {', '.join(available())}")
        cfg = _merge(cfg, yaml.safe_load(f.read_text(encoding="utf-8")))
        base = DIR / channel
    pub = cfg.setdefault("publish", {})
    cfg["_id"] = channel or ""
    cfg["_base"] = base
    cfg["_state"] = base / "state.json"
    cfg["_output"] = base / "output"
    cfg["_queue"] = base / "a_poster"
    cfg["_analytics"] = base / "analytics.json"
    cfg["_token"] = ROOT / "secrets" / (f"youtube_token_{channel}.json" if channel
                                        else "youtube_token.json")
    cfg["_client_secret"] = ROOT / "secrets" / pub.get("client_secret", "client_secret.json")
    apply_overrides(cfg)
    return cfg


# ---------- Horaires modifiés depuis le calendrier en ligne (QG /clips) ----------
OVERRIDES = ROOT / "schedule_overrides.json"


def load_overrides():
    import json
    try:
        return json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_overrides(data):
    import json
    OVERRIDES.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def apply_overrides(cfg):
    """Ajoute à cfg['publish'] ce qui a été réglé depuis le calendrier :
    horaires fixes (slots), plage (window), jours sans sortie (_off_days), clips épinglés (_pins)."""
    ov = load_overrides().get(cfg["_id"] or "main") or {}
    pub = cfg["publish"]
    if ov.get("window"):
        pub["window"] = ov["window"]
    if ov.get("slots"):
        pub["slots"] = sorted(ov["slots"])
        pub["_custom_slots"] = True
    pub["_off_days"] = sorted(ov.get("off_days") or [])
    pub["_pins"] = dict(ov.get("pins") or {})   # fichier du clip -> date ISO voulue
