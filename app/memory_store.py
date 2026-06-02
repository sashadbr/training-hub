"""Mémoire du coach : points faibles / patterns persistants de l'athlète.

Chaque entrée = un thème (ex. « Cadence vélo »), un constat observé sur
l'historique, et un conseil concret. Alimentée par l'analyse IA (analyze_sessions
dans ai.py) et/ou à la main. Injectée dans le contexte du coach pour adapter
tous ses conseils et ses plans.
"""
from __future__ import annotations

from db import get_connection, init_db


def list_memory() -> list[dict]:
    init_db()
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM coach_memory ORDER BY updated_at DESC, id DESC")]


def upsert_memory(topic: str, observation: str | None = None,
                  advice: str | None = None, source: str = "ia") -> str:
    """Crée ou met à jour une entrée (clé = topic, insensible à la casse).

    - Si rien n'a changé (constat ET conseil identiques) : on ne touche à rien.
    - Si ça évolue : on ARCHIVE l'ancienne version dans coach_memory_history
      (indélébile) avant d'écrire la nouvelle.
    Retourne 'created' | 'updated' | 'unchanged'.
    """
    if not topic or not topic.strip():
        return "unchanged"
    topic = topic.strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM coach_memory WHERE lower(topic) = lower(?)",
            (topic,)).fetchone()
        if row:
            same = ((row["observation"] or "") == (observation or "")
                    and (row["advice"] or "") == (advice or ""))
            if same:
                return "unchanged"
            # Évolution : on archive l'état précédent avant d'écraser.
            conn.execute(
                "INSERT INTO coach_memory_history "
                "(memory_id, topic, observation, advice, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (row["id"], row["topic"], row["observation"],
                 row["advice"], row["source"]))
            conn.execute(
                "UPDATE coach_memory SET observation = ?, advice = ?, "
                "source = ?, updated_at = datetime('now') WHERE id = ?",
                (observation, advice, source, row["id"]))
            return "updated"
        conn.execute(
            "INSERT INTO coach_memory (topic, observation, advice, source) "
            "VALUES (?, ?, ?, ?)", (topic, observation, advice, source))
        return "created"


def apply_insights(insights: list[dict], source: str = "ia") -> dict:
    """Applique une liste d'insights {topic, observation, advice}.

    Retourne {'created': n, 'updated': n, 'unchanged': n}.
    """
    res = {"created": 0, "updated": 0, "unchanged": 0}
    for it in insights:
        if isinstance(it, dict) and it.get("topic"):
            status = upsert_memory(it["topic"], it.get("observation"),
                                   it.get("advice"), source)
            res[status] = res.get(status, 0) + 1
    return res


def list_history(topic: str | None = None) -> list[dict]:
    """Historique archivé (versions précédentes), du plus récent au plus ancien.
    Filtrable par thème."""
    init_db()
    with get_connection() as conn:
        if topic:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM coach_memory_history WHERE lower(topic) = lower(?) "
                "ORDER BY archived_at DESC, id DESC", (topic,))]
        return [dict(r) for r in conn.execute(
            "SELECT * FROM coach_memory_history ORDER BY archived_at DESC, id DESC")]


def delete_memory(mem_id: int) -> None:
    """Supprime une entrée vivante. On archive d'abord son état (l'historique
    reste indélébile pour garder la trace de ce qui a existé)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM coach_memory WHERE id = ?", (mem_id,)).fetchone()
        if row:
            conn.execute(
                "INSERT INTO coach_memory_history "
                "(memory_id, topic, observation, advice, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (row["id"], row["topic"], row["observation"],
                 row["advice"], row["source"]))
        conn.execute("DELETE FROM coach_memory WHERE id = ?", (mem_id,))


def clear_all() -> None:
    """Vide la mémoire vivante. L'historique archivé est CONSERVÉ (indélébile)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM coach_memory")
