"""Lecture/écriture du profil athlète (ligne unique, saisie manuelle)."""
from __future__ import annotations

from db import get_connection, init_db

FIELDS = [
    "sexe", "age", "poids_kg", "taille_cm", "fc_max", "fc_repos",
    "ftp_w", "allure_seuil", "objectif_poids_kg",
]


def get_profile() -> dict:
    init_db()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    return dict(row) if row else {}


def save_profile(values: dict) -> None:
    data = {k: values.get(k) for k in FIELDS}
    cols = ", ".join(FIELDS)
    placeholders = ", ".join("?" * len(FIELDS))
    updates = ", ".join(f"{k} = excluded.{k}" for k in FIELDS)
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO profile (id, {cols}) VALUES (1, {placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, updated_at = datetime('now')",
            [data[k] for k in FIELDS],
        )
