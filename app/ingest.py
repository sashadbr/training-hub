"""Scanne inbox/, parse les .fit, insère en base (dédoublonnage par hash), archive."""
from __future__ import annotations

import shutil
from pathlib import Path

from db import (ARCHIVE_DIR, INBOX_DIR, get_connection, init_db,
                insert_returning_id)
from parser import parse_fit

ACT_COLS = [
    "file_hash", "file_name", "sport", "sub_sport", "start_time",
    "duration_s", "distance_m", "elevation_gain_m", "avg_hr", "max_hr",
    "avg_cadence", "avg_power", "avg_speed", "calories",
]


def _insert_activity(conn, summary: dict, file_hash: str) -> int:
    summary = {**summary, "file_hash": file_hash}
    values = [summary.get(c) for c in ACT_COLS]
    placeholders = ", ".join("?" * len(ACT_COLS))
    return insert_returning_id(
        conn,
        f"INSERT INTO activities ({', '.join(ACT_COLS)}) VALUES ({placeholders})",
        values,
    )


def _insert_records(conn, activity_id: int, records: list[dict]) -> None:
    rows = [
        (activity_id, r["elapsed_s"], r["hr"], r["speed"], r["cadence"],
         r["power"], r["altitude"], r["distance"], r["temperature"])
        for r in records
    ]
    conn.executemany(
        "INSERT INTO records (activity_id, elapsed_s, hr, speed, cadence, "
        "power, altitude, distance, temperature) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )


def ingest_file(path: Path) -> str:
    """Ingest un fichier. Retourne 'imported', 'duplicate' ou 'error: ...'."""
    try:
        parsed = parse_fit(path)
    except Exception as e:  # fichier corrompu / non .fit
        return f"error: {e}"

    file_hash = parsed["file_hash"]
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM activities WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if exists:
            return "duplicate"
        act_id = _insert_activity(conn, parsed["summary"], file_hash)
        _insert_records(conn, act_id, parsed["records"])
    return "imported"


def scan_inbox() -> dict:
    """Ingest tous les .fit de inbox/ (hors _archive). Archive ceux importés."""
    init_db()
    results = {"imported": [], "duplicate": [], "error": []}
    fit_files = [p for p in INBOX_DIR.glob("*.fit") if p.is_file()]
    for path in sorted(fit_files):
        status = ingest_file(path)
        if status == "imported":
            results["imported"].append(path.name)
            shutil.move(str(path), str(ARCHIVE_DIR / path.name))
        elif status == "duplicate":
            results["duplicate"].append(path.name)
            shutil.move(str(path), str(ARCHIVE_DIR / path.name))
        else:
            results["error"].append(f"{path.name} ({status})")
    return results


if __name__ == "__main__":
    res = scan_inbox()
    print(f"Importées : {len(res['imported'])} {res['imported']}")
    print(f"Doublons  : {len(res['duplicate'])} {res['duplicate']}")
    print(f"Erreurs   : {len(res['error'])} {res['error']}")
