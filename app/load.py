"""Charge d'entraînement (TRIMP) et modèle de forme CTL / ATL / TSB.

- TRIMP (Banister) : charge d'une séance à partir de la FC moyenne, de la durée
  et des FC repos/max du profil.
- CTL (fitness, moyenne mobile 42 j), ATL (fatigue, 7 j), TSB = CTL - ATL (forme).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from metrics import _num

CTL_TAU = 42  # jours — fitness
ATL_TAU = 7   # jours — fatigue


def trimp(duration_s, avg_hr, fc_max, fc_repos, sexe) -> float | None:
    """TRIMP exponentiel de Banister pour une séance."""
    duration_s, avg_hr = _num(duration_s), _num(avg_hr)
    fc_max, fc_repos = _num(fc_max), _num(fc_repos)
    if not (duration_s and avg_hr and fc_max and fc_repos) or fc_max <= fc_repos:
        return None
    hrr = (avg_hr - fc_repos) / (fc_max - fc_repos)
    hrr = min(max(hrr, 0.0), 1.0)
    if str(sexe).lower().startswith("f"):
        factor = 0.86 * math.exp(1.67 * hrr)
    else:
        factor = 0.64 * math.exp(1.92 * hrr)
    return round((duration_s / 60.0) * hrr * factor, 1)


def add_trimp(activities: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Ajoute une colonne 'trimp' au DataFrame d'activités."""
    df = activities.copy()
    if df.empty:
        df["trimp"] = []
        return df
    df["trimp"] = df.apply(
        lambda r: trimp(r["duration_s"], r["avg_hr"],
                        profile.get("fc_max"), profile.get("fc_repos"),
                        profile.get("sexe")) or 0.0,
        axis=1,
    )
    return df


def fitness_series(activities: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Série quotidienne : load, CTL, ATL, TSB, de la 1re séance à aujourd'hui."""
    df = add_trimp(activities, profile)
    if df.empty:
        return pd.DataFrame(columns=["date", "load", "ctl", "atl", "tsb"])

    df["day"] = pd.to_datetime(df["start_time"], utc=True).dt.date
    daily = df.groupby("day")["trimp"].sum()

    start = min(daily.index)
    end = date.today()
    if end < start:
        end = start
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    rows, ctl, atl = [], 0.0, 0.0
    for d in days:
        load = float(daily.get(d, 0.0))
        tsb = ctl - atl  # forme = fitness - fatigue de la veille
        ctl += (load - ctl) / CTL_TAU
        atl += (load - atl) / ATL_TAU
        rows.append({"date": d, "load": round(load, 1),
                     "ctl": round(ctl, 1), "atl": round(atl, 1),
                     "tsb": round(tsb, 1)})
    return pd.DataFrame(rows)


def form_comment(tsb: float | None) -> str:
    """Interprétation simple de la forme (TSB)."""
    if tsb is None:
        return ""
    if tsb > 15:
        return "Très frais — peut-être trop : risque de désentraînement si ça dure."
    if tsb > 5:
        return "Frais et reposé : bon moment pour une séance clé ou un test."
    if tsb >= -10:
        return "Zone productive : tu encaisses de la charge tout en progressant."
    if tsb >= -25:
        return "Fatigue marquée : surveille la récup, évite d'enchaîner trop dur."
    return "Fatigue élevée : privilégie le repos ou une sortie très facile."
