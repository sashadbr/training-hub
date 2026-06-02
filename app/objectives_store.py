"""CRUD des objectifs d'entraînement."""
from __future__ import annotations

from db import get_connection, init_db


def list_objectives(include_done: bool = True) -> list[dict]:
    init_db()
    q = "SELECT * FROM objectives"
    if not include_done:
        q += " WHERE done = 0"
    q += " ORDER BY done, COALESCE(target_date, '9999'), created_at"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(q)]


def add_objective(text: str, target_date: str | None = None) -> None:
    if not text.strip():
        return
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO objectives (text, target_date) VALUES (?, ?)",
            (text.strip(), target_date or None),
        )


def set_done(obj_id: int, done: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE objectives SET done = ? WHERE id = ?",
                     (1 if done else 0, obj_id))


def delete_objective(obj_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM objectives WHERE id = ?", (obj_id,))
