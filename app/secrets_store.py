"""Stockage de la clé API (DeepSeek) pour l'analyse IA (Phase 3).

Ordre de priorité pour la clé effective :
1. variable d'environnement DEEPSEEK_API_KEY ;
2. secret Streamlit (st.secrets) — utilisé sur Streamlit Cloud ;
3. fichier local data/secrets.json (jamais commité, voir .gitignore).
"""
from __future__ import annotations

import json
import os

from db import DATA_DIR

SECRET_KEYS = ("DEEPSEEK_API_KEY", "deepseek_api_key")


def _from_streamlit_secrets() -> str:
    """Clé éventuellement définie dans les secrets Streamlit Cloud."""
    try:
        import streamlit as st  # noqa: PLC0415
        for k in SECRET_KEYS:
            if k in st.secrets:
                return str(st.secrets[k])
    except Exception:
        pass
    return ""

SECRETS_PATH = DATA_DIR / "secrets.json"

# Réglages par défaut de l'API DeepSeek (compatible OpenAI).
DEFAULTS = {
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-v4-pro",
}


def load_secrets() -> dict:
    data = dict(DEFAULTS)
    if SECRETS_PATH.exists():
        try:
            data.update(json.loads(SECRETS_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return data


def save_secrets(values: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current = load_secrets()
    clean = {k: (v.strip() if isinstance(v, str) else v)
             for k, v in values.items() if k in DEFAULTS}
    current.update(clean)
    SECRETS_PATH.write_text(json.dumps(current, indent=2))
    try:
        SECRETS_PATH.chmod(0o600)  # lisible par le seul utilisateur
    except OSError:
        pass


def get_api_key() -> str:
    """Clé effective : env > secrets Streamlit > fichier local."""
    return (os.environ.get("DEEPSEEK_API_KEY")
            or _from_streamlit_secrets()
            or load_secrets().get("deepseek_api_key", ""))
