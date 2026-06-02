"""CRUD du plan d'entraînement (séances planifiées) + réconciliation.

Le calendrier est alimenté par le coach IA (generate_plan dans ai.py) et/ou
édité à la main. Quand une vraie séance .fit est importée, on marque la séance
planifiée correspondante (même jour, même sport) comme réalisée.
"""
from __future__ import annotations

from datetime import date

from db import get_connection, init_db

_FIELDS = ("date", "sport", "session_type", "title", "description",
           "rationale", "structure", "duration_min", "intensity")


def list_planned(start: str | None = None, end: str | None = None) -> list[dict]:
    """Séances planifiées entre deux dates ISO (incluses), triées par date."""
    init_db()
    q = "SELECT * FROM planned_sessions"
    cond, params = [], []
    if start:
        cond.append("date >= ?")
        params.append(start)
    if end:
        cond.append("date <= ?")
        params.append(end)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY date, id"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(q, params)]


def add_planned(session: dict) -> None:
    """Insère une séance planifiée à partir d'un dict (clés _FIELDS)."""
    if not session.get("date"):
        return
    clean = {k: session.get(k) for k in _FIELDS}
    cols = ", ".join(clean.keys())
    ph = ", ".join("?" for _ in clean)
    with get_connection() as conn:
        conn.execute(f"INSERT INTO planned_sessions ({cols}) VALUES ({ph})",
                     tuple(clean.values()))


def bulk_replace_future(sessions: list[dict], from_date: str | None = None) -> int:
    """Remplace les séances NON réalisées à partir de `from_date` (défaut aujourd'hui)
    par la nouvelle liste. Les séances déjà réalisées sont préservées.
    Retourne le nombre de séances insérées."""
    frm = from_date or date.today().isoformat()
    with get_connection() as conn:
        conn.execute("DELETE FROM planned_sessions WHERE done = 0 AND date >= ?",
                     (frm,))
        n = 0
        for s in sessions:
            if not s.get("date") or s["date"] < frm:
                continue
            clean = {k: s.get(k) for k in _FIELDS}
            cols = ", ".join(clean.keys())
            ph = ", ".join("?" for _ in clean)
            conn.execute(f"INSERT INTO planned_sessions ({cols}) VALUES ({ph})",
                         tuple(clean.values()))
            n += 1
    return n


def merge_sessions(sessions: list[dict]) -> tuple[int, int]:
    """Fusionne les séances proposées SANS effacer le reste du plan.

    Pour chaque séance proposée : si une séance NON réalisée existe déjà le
    même jour ET même sport, on la met à jour ; sinon on l'ajoute. Les autres
    séances (autres jours, autres sports, déjà réalisées) sont préservées.
    Retourne (ajoutées, modifiées).
    """
    added = updated = 0
    with get_connection() as conn:
        for s in sessions:
            if not s.get("date"):
                continue
            clean = {k: s.get(k) for k in _FIELDS}
            existing = conn.execute(
                "SELECT id FROM planned_sessions "
                "WHERE date = ? AND done = 0 AND IFNULL(sport, '') = IFNULL(?, '') "
                "LIMIT 1",
                (clean["date"], clean.get("sport")),
            ).fetchone()
            if existing:
                set_clause = ", ".join(f"{k} = ?" for k in _FIELDS)
                conn.execute(
                    f"UPDATE planned_sessions SET {set_clause} WHERE id = ?",
                    tuple(clean[k] for k in _FIELDS) + (existing["id"],))
                updated += 1
            else:
                cols = ", ".join(clean.keys())
                ph = ", ".join("?" for _ in clean)
                conn.execute(
                    f"INSERT INTO planned_sessions ({cols}) VALUES ({ph})",
                    tuple(clean.values()))
                added += 1
    return added, updated


def set_done(plan_id: int, done: bool, activity_id: int | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE planned_sessions SET done = ?, activity_id = ? WHERE id = ?",
            (1 if done else 0, activity_id, plan_id),
        )


def delete_planned(plan_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM planned_sessions WHERE id = ?", (plan_id,))


def clear_all() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM planned_sessions")


def reconcile_with_activities() -> int:
    """Marque comme réalisées les séances planifiées dont le jour ET le sport
    correspondent à une vraie séance importée. Retourne le nombre de liaisons."""
    init_db()
    linked = 0
    with get_connection() as conn:
        pending = conn.execute(
            "SELECT id, date, sport FROM planned_sessions WHERE done = 0"
        ).fetchall()
        for p in pending:
            row = conn.execute(
                "SELECT id FROM activities "
                "WHERE substr(start_time, 1, 10) = ? "
                "AND (sport = ? OR sub_sport = ? OR ? IS NULL) "
                "ORDER BY start_time LIMIT 1",
                (p["date"], p["sport"], p["sport"], p["sport"]),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE planned_sessions SET done = 1, activity_id = ? WHERE id = ?",
                    (row["id"], p["id"]),
                )
                linked += 1
    return linked
