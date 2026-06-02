"""Calculs dérivés : zones FC, allure, calories, dérive cardiaque."""
from __future__ import annotations

import math


def _num(x):
    """Retourne un float exploitable, ou None si None/NaN."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f

# Bornes de zones en % de la FC max (modèle 5 zones classique)
HR_ZONE_BOUNDS = [0.50, 0.60, 0.70, 0.80, 0.90, 1.01]
HR_ZONE_LABELS = ["Z1 récup", "Z2 endurance", "Z3 tempo", "Z4 seuil", "Z5 VO2max"]


def hr_zones(fc_max: int) -> list[tuple[str, int, int]]:
    """Retourne [(label, bpm_min, bpm_max), ...] pour une FC max donnée."""
    zones = []
    for i, label in enumerate(HR_ZONE_LABELS):
        lo = round(fc_max * HR_ZONE_BOUNDS[i])
        hi = round(fc_max * HR_ZONE_BOUNDS[i + 1]) - 1
        zones.append((label, lo, hi))
    return zones


def time_in_zones(hr_values: list[int | None], fc_max: int) -> dict[str, int]:
    """Secondes passées dans chaque zone (1 échantillon ~ 1 s)."""
    counts = {label: 0 for label in HR_ZONE_LABELS}
    for hr in hr_values:
        if hr is None or fc_max <= 0:
            continue
        frac = hr / fc_max
        for i, label in enumerate(HR_ZONE_LABELS):
            if HR_ZONE_BOUNDS[i] <= frac < HR_ZONE_BOUNDS[i + 1]:
                counts[label] += 1
                break
        else:
            if frac >= HR_ZONE_BOUNDS[-2]:
                counts[HR_ZONE_LABELS[-1]] += 1
    return counts


def pace_sec_per_km(speed_ms: float | None) -> float | None:
    """m/s -> secondes par km."""
    speed_ms = _num(speed_ms)
    if not speed_ms or speed_ms <= 0:
        return None
    return 1000.0 / speed_ms


def format_pace(sec_per_km: float | None) -> str:
    sec_per_km = _num(sec_per_km)
    if not sec_per_km or sec_per_km <= 0:
        return "—"
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d}/km"


def format_duration(seconds: float | None) -> str:
    seconds = _num(seconds)
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}" if h else f"{m}:{s:02d}"


def cardiac_drift(hr_values: list[int | None], speed_values: list[float | None]) -> float | None:
    """Dérive du ratio FC/allure entre 1re et 2e moitié, en %.
    Positif = FC qui grimpe à effort égal (fatigue / chaleur)."""
    paired = [
        (h, s) for hr, sp in zip(hr_values, speed_values)
        if (h := _num(hr)) and (s := _num(sp)) and s > 0.5
    ]
    if len(paired) < 60:
        return None
    mid = len(paired) // 2
    def ratio(seg):
        hrs = sum(p[0] for p in seg) / len(seg)
        sps = sum(p[1] for p in seg) / len(seg)
        return hrs / sps
    r1, r2 = ratio(paired[:mid]), ratio(paired[mid:])
    if r1 == 0:
        return None
    return round((r2 - r1) / r1 * 100, 1)


def estimate_calories(duration_s, avg_hr, poids_kg, age, sexe) -> int | None:
    """Estimation kcal via la formule de Keytel (FC, poids, âge, sexe)."""
    duration_s, avg_hr = _num(duration_s), _num(avg_hr)
    poids_kg, age = _num(poids_kg), _num(age)
    if not (duration_s and avg_hr and poids_kg and age and sexe):
        return None
    minutes = duration_s / 60.0
    if str(sexe).lower().startswith("f"):
        kcal_min = (-20.4022 + 0.4472 * avg_hr - 0.1263 * poids_kg + 0.074 * age) / 4.184
    else:
        kcal_min = (-55.0969 + 0.6309 * avg_hr + 0.1988 * poids_kg + 0.2017 * age) / 4.184
    return max(0, round(kcal_min * minutes))
