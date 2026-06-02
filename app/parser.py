"""Lecture d'un fichier .fit (Coros/Garmin) -> résumé + échantillons.

Utilise le SDK officiel Garmin (garmin-fit-sdk), robuste sur les fichiers
récents (champs developer data) là où fitparse échoue.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from garmin_fit_sdk import Decoder, Stream


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pick(msg: dict, *names):
    """Première valeur non nulle parmi plusieurs noms de champ candidats."""
    for name in names:
        val = msg.get(name)
        if val is not None:
            return val
    return None


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(dt)


def parse_fit(path: str | Path) -> dict:
    """Retourne {'summary': {...}, 'records': [...], 'file_hash': str}."""
    path = Path(path)
    messages, _errors = Decoder(Stream.from_file(str(path))).read(
        convert_datetimes_to_dates=True
    )

    # --- Résumé (message session) ---
    summary: dict = {}
    sessions = messages.get("session_mesgs") or []
    if sessions:
        s = sessions[-1]
        summary = {
            "sport": s.get("sport"),
            "sub_sport": s.get("sub_sport"),
            "start_time": _iso(s.get("start_time")),
            "duration_s": _pick(s, "total_timer_time", "total_elapsed_time"),
            "distance_m": s.get("total_distance"),
            "elevation_gain_m": s.get("total_ascent"),
            "avg_hr": s.get("avg_heart_rate"),
            "max_hr": s.get("max_heart_rate"),
            "avg_cadence": _pick(s, "avg_running_cadence", "avg_cadence"),
            "avg_power": s.get("avg_power") or None,
            "avg_speed": _pick(s, "enhanced_avg_speed", "avg_speed"),
            "calories": s.get("total_calories"),
        }

    # --- Échantillons (messages record) ---
    records: list[dict] = []
    start_ts = None
    for r in messages.get("record_mesgs") or []:
        ts = r.get("timestamp")
        if start_ts is None and ts is not None:
            start_ts = ts
        elapsed = (ts - start_ts).total_seconds() if (ts and start_ts) else None
        records.append({
            "elapsed_s": elapsed,
            "hr": r.get("heart_rate"),
            "speed": _pick(r, "enhanced_speed", "speed"),
            "cadence": r.get("cadence"),
            "power": r.get("power"),
            "altitude": _pick(r, "enhanced_altitude", "altitude"),
            "distance": r.get("distance"),
            "temperature": r.get("temperature"),
        })

    # Fallback résumé depuis les records si pas de session
    if not summary and records:
        summary = {
            "sport": None, "sub_sport": None, "start_time": _iso(start_ts),
            "duration_s": records[-1]["elapsed_s"], "distance_m": records[-1]["distance"],
            "elevation_gain_m": None, "avg_hr": None, "max_hr": None,
            "avg_cadence": None, "avg_power": None, "avg_speed": None, "calories": None,
        }

    summary["file_name"] = path.name
    return {"summary": summary, "records": records, "file_hash": _hash_file(path)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python parser.py <fichier.fit>")
        raise SystemExit(1)
    out = parse_fit(sys.argv[1])
    s = out["summary"]
    print(f"Sport      : {s.get('sport')} / {s.get('sub_sport')}")
    print(f"Début      : {s.get('start_time')}")
    print(f"Durée      : {s.get('duration_s')} s")
    print(f"Distance   : {s.get('distance_m')} m")
    print(f"D+         : {s.get('elevation_gain_m')} m")
    print(f"FC moy/max : {s.get('avg_hr')} / {s.get('max_hr')}")
    print(f"Cadence    : {s.get('avg_cadence')}  Puissance: {s.get('avg_power')}")
    print(f"Échantillons : {len(out['records'])}")
