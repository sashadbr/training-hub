"""Training Hub — dashboard local (Streamlit)."""
from __future__ import annotations

import calendar as _cal
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import metrics as M
import load as L
import ai as AI
from db import IS_PG, get_connection, init_db, read_df
from ingest import ingest_file, scan_inbox
from profile_store import get_profile, save_profile
from secrets_store import load_secrets, save_secrets
from objectives_store import (add_objective, delete_objective, list_objectives,
                              set_done)
import plan_store as P
import memory_store as MEM

st.set_page_config(page_title="Training Hub", page_icon="🏔️", layout="wide")
init_db()

# --- Adaptation mobile : marges réduites + grilles calendrier responsives ---
st.markdown("""
<style>
/* Règles de base (desktop) — placées AVANT la media query pour que celle-ci
   l'emporte sur mobile (une media query n'ajoute pas de spécificité). */
.wd-full {display: inline;}
.wd-abbr {display: none;}
@media (max-width: 640px) {
  .block-container {padding: 1rem 0.6rem 3rem !important;}
  h1 {font-size: 1.5rem !important;}
  h2 {font-size: 1.2rem !important;}
  h3 {font-size: 1.05rem !important;}
  /* Vue Année : 2 mois par ligne sur téléphone au lieu de 3 */
  .cal-grid {grid-template-columns: repeat(2, 1fr) !important; gap: 10px !important;}
  /* Vue Mois : cases plus basses, texte plus petit */
  .m-cal td {height: 64px !important; padding: 2px !important;}
  .m-cal th {padding: 3px !important; font-size: 10px !important;}
  .m-chip {font-size: 9px !important; padding: 1px 3px !important;}
  .m-day {font-size: 11px !important;}
  /* Noms de jours abrégés en mobile */
  .wd-full {display: none;}
  .wd-abbr {display: inline;}
}
</style>
""", unsafe_allow_html=True)

def _require_password() -> None:
    """Verrou par mot de passe pour l'hébergement public.

    Le mot de passe attendu vient des secrets Streamlit (clé ``APP_PASSWORD``)
    ou de la variable d'environnement ``APP_PASSWORD``. Si aucun n'est défini
    (cas de l'usage local sur le Mac), l'accès reste libre.
    """
    expected = ""
    try:
        if "APP_PASSWORD" in st.secrets:
            expected = str(st.secrets["APP_PASSWORD"])
    except Exception:
        pass
    expected = expected or os.environ.get("APP_PASSWORD", "")
    if not expected or st.session_state.get("_auth_ok"):
        return
    st.title("🔒 Training Hub")
    st.caption("Entre le mot de passe pour accéder à ton coach.")
    pwd = st.text_input("Mot de passe", type="password",
                        label_visibility="collapsed")
    if st.button("Entrer", type="primary"):
        if pwd == expected:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()


_require_password()

SPORT_ICON = {"running": "🏃", "cycling": "🚴", "trail": "⛰️"}


# --------------------------------------------------------------------------- #
# Accès données
# --------------------------------------------------------------------------- #
def load_activities() -> pd.DataFrame:
    with get_connection() as conn:
        df = read_df(conn, "SELECT * FROM activities ORDER BY start_time DESC")
    if not df.empty:
        df["start_time"] = pd.to_datetime(df["start_time"], utc=True, format="ISO8601")
        df["km"] = (df["distance_m"].fillna(0) / 1000).round(2)
        df["pace"] = df["avg_speed"].apply(lambda s: M.format_pace(M.pace_sec_per_km(s)))
        df["dur"] = df["duration_s"].apply(M.format_duration)
    return df


def load_records(activity_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        return read_df(
            conn,
            "SELECT * FROM records WHERE activity_id = ? ORDER BY elapsed_s",
            (activity_id,),
        )


def label_for(row) -> str:
    icon = SPORT_ICON.get(row["sub_sport"]) or SPORT_ICON.get(row["sport"], "🏅")
    when = row["start_time"].strftime("%d/%m/%Y %H:%M")
    return f"{icon} {when} · {row['km']} km · {row['dur']}"


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_overview():
    st.title("🏔️ Vue d'ensemble")
    df = load_activities()
    if df.empty:
        st.info("Aucune séance pour l'instant. Dépose des fichiers `.fit` dans "
                "`inbox/` puis clique sur **Importer les nouvelles séances** "
                "dans la barre latérale.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Séances", len(df))
    c2.metric("Distance totale", f"{df['km'].sum():.0f} km")
    c3.metric("Temps total", M.format_duration(df["duration_s"].sum()))
    c4.metric("D+ total", f"{df['elevation_gain_m'].fillna(0).sum():.0f} m")

    # Volume hebdomadaire
    st.subheader("Volume hebdomadaire")
    weekly = (
        df.set_index("start_time")
        .resample("W-MON")["km"].sum()
        .reset_index()
    )
    weekly["semaine"] = weekly["start_time"].dt.strftime("%d/%m")
    fig = go.Figure(go.Bar(x=weekly["semaine"], y=weekly["km"], marker_color="#2e7d32"))
    fig.update_layout(height=280, margin=dict(t=10, b=10), yaxis_title="km")
    st.plotly_chart(fig, width="stretch")

    # Tableau récent
    st.subheader("Séances récentes")
    show = df[["start_time", "sport", "sub_sport", "km", "dur", "pace",
               "elevation_gain_m", "avg_hr", "max_hr"]].copy()
    show.columns = ["Date", "Sport", "Type", "km", "Durée", "Allure",
                    "D+", "FC moy", "FC max"]
    show["Date"] = show["Date"].dt.strftime("%d/%m/%Y %H:%M")
    st.dataframe(show, width="stretch", hide_index=True)


def page_session():
    st.title("📈 Analyse de séance")
    df = load_activities()
    if df.empty:
        st.info("Aucune séance à analyser.")
        return

    options = {label_for(r): r["id"] for _, r in df.iterrows()}
    choice = st.selectbox("Choisis une séance", list(options.keys()))
    act = df[df["id"] == options[choice]].iloc[0]
    prof = get_profile()
    fc_max = prof.get("fc_max")

    def num(v):
        return M._num(v)

    def fmt(v, suffix="", dec=0):
        v = num(v)
        return f"{v:.{dec}f}{suffix}" if v is not None else "—"

    is_bike = (act["sport"] == "cycling")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Distance", f"{act['km']} km")
    c2.metric("Durée", act["dur"])
    c3.metric("Vitesse moy" if is_bike else "Allure moy",
              f"{num(act['avg_speed']) * 3.6:.1f} km/h" if (is_bike and num(act['avg_speed'])) else act["pace"])
    c4.metric("D+", fmt(act["elevation_gain_m"], " m"))
    c1.metric("FC moy", fmt(act["avg_hr"]))
    c2.metric("FC max", fmt(act["max_hr"]))
    c3.metric("Cadence moy", fmt(act["avg_cadence"]))
    cal = num(act["calories"]) or M.estimate_calories(
        act["duration_s"], act["avg_hr"], prof.get("poids_kg"),
        prof.get("age"), prof.get("sexe"))
    c4.metric("Calories", fmt(cal))

    rec = load_records(int(act["id"]))
    if rec.empty:
        st.warning("Pas d'échantillons détaillés pour cette séance.")
        return

    rec["min"] = rec["elapsed_s"] / 60
    rec["pace_s"] = rec["speed"].apply(M.pace_sec_per_km)
    rec["kmh"] = rec["speed"] * 3.6

    # Graphes
    if rec["hr"].notna().any():
        fig = go.Figure(go.Scatter(x=rec["min"], y=rec["hr"], line=dict(color="#d32f2f")))
        fig.update_layout(title="Fréquence cardiaque", height=240,
                          margin=dict(t=30, b=10), xaxis_title="min", yaxis_title="bpm")
        st.plotly_chart(fig, width="stretch")

    if is_bike and rec["kmh"].notna().any():
        fig = go.Figure(go.Scatter(x=rec["min"], y=rec["kmh"], line=dict(color="#1976d2")))
        fig.update_layout(title="Vitesse", height=240, margin=dict(t=30, b=10),
                          xaxis_title="min", yaxis_title="km/h")
        st.plotly_chart(fig, width="stretch")
    elif rec["pace_s"].notna().any():
        fig = go.Figure(go.Scatter(x=rec["min"], y=rec["pace_s"], line=dict(color="#1976d2")))
        fig.update_layout(title="Allure", height=240, margin=dict(t=30, b=10),
                          xaxis_title="min", yaxis_title="s/km",
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch")

    if rec["power"].notna().any():
        fig = go.Figure(go.Scatter(x=rec["min"], y=rec["power"], line=dict(color="#f57c00")))
        fig.update_layout(title="Puissance", height=220, margin=dict(t=30, b=10),
                          xaxis_title="min", yaxis_title="W")
        st.plotly_chart(fig, width="stretch")

    if rec["cadence"].notna().any():
        fig = go.Figure(go.Scatter(x=rec["min"], y=rec["cadence"], line=dict(color="#7b1fa2")))
        unit = "tr/min" if is_bike else "pas/min"
        fig.update_layout(title="Cadence", height=200, margin=dict(t=30, b=10),
                          xaxis_title="min", yaxis_title=unit)
        st.plotly_chart(fig, width="stretch")

    if rec["altitude"].notna().any():
        fig = go.Figure(go.Scatter(x=rec["min"], y=rec["altitude"], fill="tozeroy",
                                   line=dict(color="#6d4c41")))
        fig.update_layout(title="Altitude", height=200, margin=dict(t=30, b=10),
                          xaxis_title="min", yaxis_title="m")
        st.plotly_chart(fig, width="stretch")

    # Zones FC + dérive cardiaque
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Temps en zones")
        if fc_max:
            tiz = M.time_in_zones(rec["hr"].tolist(), fc_max)
            tdf = pd.DataFrame({"Zone": list(tiz.keys()),
                                "min": [round(v / 60, 1) for v in tiz.values()]})
            fig = go.Figure(go.Bar(x=tdf["min"], y=tdf["Zone"], orientation="h",
                                   marker_color="#2e7d32"))
            fig.update_layout(height=240, margin=dict(t=10, b=10), xaxis_title="min")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Renseigne ta **FC max** dans Profil pour voir les zones.")
    with col_b:
        st.subheader("Dérive cardiaque")
        drift = M.cardiac_drift(rec["hr"].tolist(), rec["speed"].tolist())
        if drift is None:
            st.write("Données insuffisantes.")
        else:
            st.metric("FC/allure 2e moitié vs 1re", f"{drift:+.1f} %")
            if drift > 5:
                st.caption("Dérive marquée : fatigue, chaleur ou allure trop rapide.")
            elif drift < 0:
                st.caption("FC stable/descendante : bonne gestion de l'effort.")
            else:
                st.caption("Dérive normale.")


def page_profile():
    st.title("👤 Profil")
    st.caption("Saisis tes données — elles servent à calculer zones, calories et charge.")
    p = get_profile()

    c1, c2 = st.columns(2)
    with c1:
        sexe = st.selectbox("Sexe", ["M", "F"],
                            index=0 if (p.get("sexe") or "M") == "M" else 1)
        age = st.number_input("Âge", 10, 100, int(p.get("age") or 25))
        poids = st.number_input("Poids (kg)", 30.0, 200.0, float(p.get("poids_kg") or 70.0), step=0.5)
        taille = st.number_input("Taille (cm)", 120.0, 230.0, float(p.get("taille_cm") or 175.0), step=0.5)
        obj = st.number_input("Objectif de poids (kg, 0 = aucun)", 0.0, 200.0,
                              float(p.get("objectif_poids_kg") or 0.0), step=0.5)
    with c2:
        fc_max = st.number_input("FC max (bpm)", 120, 230, int(p.get("fc_max") or 190))
        fc_repos = st.number_input("FC repos (bpm)", 30, 100, int(p.get("fc_repos") or 55))
        ftp = st.number_input("FTP vélo (watts)", 50, 600, int(p.get("ftp_w") or 200))
        st.write("Allure seuil course")
        sa = p.get("allure_seuil") or 300
        pc1, pc2 = st.columns(2)
        mn = pc1.number_input("min/km", 2, 12, sa // 60)
        sc = pc2.number_input("sec", 0, 59, sa % 60)

    if st.button("💾 Enregistrer", type="primary"):
        save_profile({
            "sexe": sexe, "age": age, "poids_kg": poids, "taille_cm": taille,
            "fc_max": fc_max, "fc_repos": fc_repos, "ftp_w": ftp,
            "allure_seuil": mn * 60 + sc,
            "objectif_poids_kg": obj or None,
        })
        st.success("Profil enregistré.")

    if fc_max:
        st.subheader("Tes zones de fréquence cardiaque")
        zdf = pd.DataFrame(M.hr_zones(fc_max), columns=["Zone", "min bpm", "max bpm"])
        st.dataframe(zdf, width="stretch", hide_index=True)


def page_form():
    st.title("📊 Forme & charge")
    st.caption("Modèle de charge basé sur la FC (TRIMP) : fitness (CTL), "
               "fatigue (ATL) et forme (TSB = CTL − ATL).")
    prof = get_profile()
    if not (prof.get("fc_max") and prof.get("fc_repos")):
        st.info("Renseigne ta **FC max** et ta **FC repos** dans Profil pour "
                "calculer ta charge.")
        return
    df = load_activities()
    fs = L.fitness_series(df, prof)
    if fs.empty:
        st.info("Aucune séance pour calculer la charge.")
        return

    last = fs.iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Fitness (CTL)", f"{last['ctl']:.0f}")
    c2.metric("Fatigue (ATL)", f"{last['atl']:.0f}")
    c3.metric("Forme (TSB)", f"{last['tsb']:+.0f}")
    st.caption(L.form_comment(last["tsb"]))

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fs["date"], y=fs["ctl"], name="Fitness (CTL)",
                             line=dict(color="#2e7d32")))
    fig.add_trace(go.Scatter(x=fs["date"], y=fs["atl"], name="Fatigue (ATL)",
                             line=dict(color="#f57c00")))
    fig.update_layout(title="Fitness vs fatigue", height=300,
                      margin=dict(t=30, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")

    colors = ["#2e7d32" if v >= 0 else "#d32f2f" for v in fs["tsb"]]
    fig = go.Figure(go.Bar(x=fs["date"], y=fs["tsb"], marker_color=colors))
    fig.update_layout(title="Forme (TSB) — positif = frais, négatif = fatigué",
                      height=240, margin=dict(t=30, b=10))
    st.plotly_chart(fig, width="stretch")

    with st.expander("Charge quotidienne (TRIMP)"):
        st.bar_chart(fs.set_index("date")["load"])


def page_records():
    st.title("🏅 Records perso")
    df = load_activities()
    if df.empty:
        st.info("Aucune séance.")
        return
    df["kmh"] = df["avg_speed"].apply(lambda s: (M._num(s) or 0) * 3.6)
    df["pace_s"] = df["avg_speed"].apply(M.pace_sec_per_km)
    for sport, g in df.groupby("sport"):
        icon = SPORT_ICON.get(sport, "🏅")
        st.subheader(f"{icon} {sport}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Plus longue distance", f"{g['km'].max():.1f} km")
        c2.metric("Plus longue durée", M.format_duration(g["duration_s"].max()))
        c3.metric("Plus gros D+", f"{g['elevation_gain_m'].max():.0f} m")
        if sport == "cycling":
            c4.metric("Vitesse moy max", f"{g['kmh'].max():.1f} km/h")
        else:
            best = g["pace_s"].min()
            c4.metric("Meilleure allure moy", M.format_pace(best))


def page_progression():
    st.title("📈 Progression")
    df = load_activities()
    if df.empty:
        st.info("Aucune séance.")
        return
    sports = sorted(df["sport"].dropna().unique())
    sport = st.selectbox("Sport", sports)
    g = df[df["sport"] == sport].sort_values("start_time")
    is_bike = (sport == "cycling")
    g = g.assign(kmh=g["avg_speed"].apply(lambda s: (M._num(s) or 0) * 3.6))

    if len(g) < 2:
        st.info("Il faut au moins 2 séances de ce sport pour voir une tendance. "
                "Importe d'autres `.fit` pour suivre ta progression.")
    metric = ("Vitesse moy (km/h)", "kmh") if is_bike else ("Allure moy", None)
    fig = go.Figure(go.Scatter(x=g["start_time"], y=g["km"], mode="lines+markers",
                               line=dict(color="#2e7d32")))
    fig.update_layout(title="Distance par séance", height=260,
                      margin=dict(t=30, b=10), yaxis_title="km")
    st.plotly_chart(fig, width="stretch")

    if is_bike:
        fig = go.Figure(go.Scatter(x=g["start_time"], y=g["kmh"], mode="lines+markers",
                                   line=dict(color="#1976d2")))
        fig.update_layout(title="Vitesse moyenne", height=260,
                          margin=dict(t=30, b=10), yaxis_title="km/h")
        st.plotly_chart(fig, width="stretch")

    if g["avg_hr"].notna().any():
        fig = go.Figure(go.Scatter(x=g["start_time"], y=g["avg_hr"], mode="lines+markers",
                                   line=dict(color="#d32f2f")))
        fig.update_layout(title="FC moyenne", height=260,
                          margin=dict(t=30, b=10), yaxis_title="bpm")
        st.plotly_chart(fig, width="stretch")


def page_settings():
    st.title("⚙️ Réglages")
    st.subheader("Analyse IA — clé API DeepSeek")
    st.caption("Pour la Phase 3 (analyse de séance, nutrition, coaching). "
               "La clé est stockée en local dans `data/secrets.json` (non versionné).")
    s = load_secrets()
    key = st.text_input("Clé API DeepSeek", value=s.get("deepseek_api_key", ""),
                        type="password", placeholder="sk-...")
    base = st.text_input("URL de base", value=s.get("deepseek_base_url", ""))
    model = st.text_input("Modèle", value=s.get("deepseek_model", ""))
    if st.button("💾 Enregistrer", type="primary"):
        save_secrets({"deepseek_api_key": key, "deepseek_base_url": base,
                      "deepseek_model": model})
        st.success("Réglages enregistrés.")
    if s.get("deepseek_api_key"):
        st.caption("✅ Une clé est enregistrée. L'analyse IA sera activée en Phase 3.")
    else:
        st.caption("Aucune clé pour l'instant — tu pourras la coller ici quand tu veux.")


def page_objectives():
    st.title("🎯 Objectifs")
    st.caption("Définis tes objectifs d'entraînement. Le coach IA en tient compte "
               "dans ses recommandations.")

    with st.form("add_obj", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        text = c1.text_input("Nouvel objectif",
                             placeholder="Ex : courir un 10 km en moins de 45 min")
        date_str = c2.text_input("Échéance (optionnel)", placeholder="2026-09-15")
        if st.form_submit_button("➕ Ajouter", type="primary") and text.strip():
            add_objective(text, date_str.strip() or None)
            st.rerun()

    objs = list_objectives(include_done=True)
    if not objs:
        st.info("Aucun objectif pour l'instant.")
        return

    actifs = [o for o in objs if not o["done"]]
    faits = [o for o in objs if o["done"]]

    if actifs:
        st.subheader("En cours")
        for o in actifs:
            c1, c2, c3 = st.columns([6, 1, 1])
            ech = f"  ·  échéance {o['target_date']}" if o["target_date"] else ""
            c1.write(f"**{o['text']}**{ech}")
            if c2.button("✅", key=f"done_{o['id']}", help="Marquer comme atteint"):
                set_done(o["id"], True)
                st.rerun()
            if c3.button("🗑️", key=f"del_{o['id']}", help="Supprimer"):
                delete_objective(o["id"])
                st.rerun()

    if faits:
        st.subheader("Atteints")
        for o in faits:
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.write(f"~~{o['text']}~~")
            if c2.button("↩️", key=f"undo_{o['id']}", help="Remettre en cours"):
                set_done(o["id"], False)
                st.rerun()
            if c3.button("🗑️", key=f"del_{o['id']}", help="Supprimer"):
                delete_objective(o["id"])
                st.rerun()


def page_memory():
    st.title("🧠 Mémoire du coach")
    st.caption("Tes points faibles et habitudes, mémorisés et réutilisés par l'IA dans "
               "tous ses conseils et tes plans. Ex : « cadence vélo trop basse → "
               "travailler la cadence ».")

    with st.container(border=True):
        st.subheader("🔍 Analyser mes séances")
        if not AI.get_api_key():
            st.warning("⚠️ Aucune clé API DeepSeek. Va dans **Réglages** pour en ajouter une.")
        focus = st.text_input(
            "Sur quoi veux-tu que l'IA se concentre ? (optionnel)",
            placeholder="Ex : regarde surtout ma cadence et ma gestion d'allure à vélo")
        if st.button("🔍 Analyser et mettre à jour la mémoire", type="primary"):
            with st.spinner("L'IA analyse ton historique…"):
                insights, msg = AI.analyze_sessions(focus)
            if insights:
                r = MEM.apply_insights(insights)
                st.success(
                    f"{msg} — {r['created']} nouveau(x), {r['updated']} mis à jour "
                    f"(ancienne version archivée), {r['unchanged']} déjà à jour.")
                st.rerun()
            else:
                st.error(msg)
        st.caption("L'IA étudie tes moyennes par sport, la tendance de cadence vélo, "
                   "la FC, le D+… pour repérer ce qui revient et l'enregistrer ici.")

    # Ajout manuel
    with st.expander("➕ Ajouter un point à la main"):
        with st.form("add_mem", clear_on_submit=True):
            topic = st.text_input("Thème", placeholder="Ex : Cadence vélo")
            obs = st.text_input("Constat", placeholder="Ex : cadence moyenne basse (~78 rpm)")
            adv = st.text_input("Conseil", placeholder="Ex : viser 85-90 rpm")
            if st.form_submit_button("Ajouter") and topic.strip():
                MEM.upsert_memory(topic, obs or None, adv or None, source="manuel")
                st.rerun()

    mem = MEM.list_memory()
    if not mem:
        st.info("Mémoire vide. Lance une analyse ou ajoute un point à la main.")
        return
    st.subheader("Points mémorisés")
    for m in mem:
        c1, c2 = st.columns([10, 1])
        with c1:
            tag = "🤖" if m["source"] == "ia" else "✍️"
            st.markdown(f"{tag} **{m['topic']}**")
            if m.get("observation"):
                st.caption(f"Constat : {m['observation']}")
            if m.get("advice"):
                st.markdown(f"→ _{m['advice']}_")
            hist = MEM.list_history(m["topic"])
            if hist:
                with st.expander(f"🕰️ Évolution ({len(hist)} version(s) précédente(s))"):
                    for h in hist:
                        when = (h.get("archived_at") or "")[:16]
                        st.markdown(f"**{when}** _(archivé)_")
                        if h.get("observation"):
                            st.caption(f"Constat : {h['observation']}")
                        if h.get("advice"):
                            st.markdown(f"→ _{h['advice']}_")
                        st.markdown("---")
        if c2.button("🗑️", key=f"mem_del_{m['id']}", help="Oublier ce point"):
            MEM.delete_memory(m["id"])
            st.rerun()
        st.divider()

    # Historique global des thèmes qui ne sont plus dans la mémoire vivante
    live_topics = {m["topic"].lower() for m in mem}
    all_hist = MEM.list_history()
    orphan_topics = sorted({h["topic"] for h in all_hist
                            if h["topic"].lower() not in live_topics})
    if orphan_topics:
        with st.expander(f"🗄️ Archives d'anciens points ({len(orphan_topics)} thème(s) "
                         "retiré(s) de la mémoire active)"):
            st.caption("Trace indélébile de ce qui a déjà été suivi, pour voir l'évolution.")
            for topic in orphan_topics:
                st.markdown(f"**{topic}**")
                for h in MEM.list_history(topic):
                    when = (h.get("archived_at") or "")[:16]
                    line = f"- {when}"
                    if h.get("observation"):
                        line += f" · {h['observation']}"
                    if h.get("advice"):
                        line += f" → _{h['advice']}_"
                    st.markdown(line)
                st.markdown("---")


def page_coach():
    st.title("🤖 Coach IA")
    st.caption("Ton coach DeepSeek : demande-lui ta prochaine séance, un plan sur "
               "plusieurs semaines, ou raconte-lui ton entraînement.")

    if not AI.get_api_key():
        st.warning("⚠️ Aucune clé API DeepSeek. Va dans **Réglages** pour en ajouter une.")

    if "chat" not in st.session_state:
        st.session_state.chat = []

    # Actions rapides
    st.write("**Suggestions rapides :**")
    qc1, qc2, qc3, qc4 = st.columns(4)
    quick = None
    if qc1.button("Séance du jour", width="stretch"):
        quick = ("Compte tenu de ma forme actuelle, quelle séance me conseilles-tu "
                 "aujourd'hui ? Explique-moi pourquoi.")
    if qc2.button("Plan 1 semaine", width="stretch"):
        quick = ("Propose-moi un plan d'entraînement structuré pour la semaine à venir, "
                 "jour par jour, en tenant compte de ma forme et de mes objectifs.")
    if qc3.button("Plan 1 mois", width="stretch"):
        quick = ("Propose-moi une projection d'entraînement sur le mois à venir, "
                 "structurée par semaine, avec la logique de progression.")
    if qc4.button("🧹 Effacer", width="stretch"):
        st.session_state.chat = []
        st.rerun()

    # Historique
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prefill = st.session_state.pop("coach_prefill", None)
    prompt = st.chat_input("Pose ta question au coach…") or quick or prefill
    if prompt:
        st.session_state.chat.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        context = AI.gather_context()
        with st.chat_message("assistant"):
            reply = st.write_stream(AI.stream_chat(st.session_state.chat, context))
        # On stocke la version sans les blocs techniques (plan/memory).
        st.session_state.chat.append(
            {"role": "assistant", "content": AI.strip_blocks(reply) or reply})

        # Si le coach a proposé une mise à jour du calendrier, on l'applique.
        sessions = AI.extract_plan_block(reply)
        if sessions:
            added, updated = P.merge_sessions(sessions)
            st.success(f"📅 Calendrier mis à jour : {added} ajoutée(s), "
                       f"{updated} modifiée(s). Va dans **Calendrier** pour les voir.")

        # Si le coach a noté un point en mémoire, on l'enregistre.
        insights = AI.extract_memory_block(reply)
        if insights:
            r = MEM.apply_insights(insights)
            changed = r["created"] + r["updated"]
            if changed:
                st.success(
                    f"🧠 Mémoire mise à jour : {r['created']} nouveau(x), "
                    f"{r['updated']} modifié(s) (ancienne version archivée). "
                    "Va dans **Mémoire** pour les voir.")


SPORT_EMOJI_PLAN = {"running": "🏃", "cycling": "🚴", "trail": "⛰️"}


def _session_emoji(p: dict) -> str:
    """Icône d'une séance planifiée : 😴 pour repos/récup, sinon selon le sport."""
    st_type = (p.get("session_type") or "").lower()
    if "repos" in st_type or "récup" in st_type or "recup" in st_type:
        return "😴"
    return SPORT_EMOJI_PLAN.get(p.get("sport"), "🏅")
MONTHS_FR = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
             "Août", "Septembre", "Octobre", "Novembre", "Décembre"]


def _render_year(year: int, by_day: dict[str, dict]):
    """Affiche les 12 mois de l'année en grille HTML, jours colorés selon le plan."""
    today = date.today().isoformat()
    css = """
    <style>
    .cal-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;}
    .cal-month{border:1px solid #e0e0e0;border-radius:8px;padding:8px;}
    .cal-month h4{margin:0 0 6px;font-size:14px;text-align:center;color:#333;}
    .cal-month table{width:100%;border-collapse:collapse;font-size:11px;}
    .cal-month td{height:22px;text-align:center;border-radius:4px;color:#444;}
    .cal-wd td{color:#999;font-weight:600;}
    .d-done{background:#2e7d32;color:#fff;font-weight:600;}
    .d-plan{background:#bbdefb;color:#0d47a1;font-weight:600;}
    .d-today{outline:2px solid #d32f2f;}
    </style>
    """
    html = [css, '<div class="cal-grid">']
    cal = _cal.Calendar(firstweekday=0)
    for m in range(1, 13):
        html.append('<div class="cal-month">')
        html.append(f"<h4>{MONTHS_FR[m - 1]}</h4>")
        html.append("<table>")
        html.append('<tr class="cal-wd"><td>L</td><td>M</td><td>M</td><td>J</td>'
                    "<td>V</td><td>S</td><td>D</td></tr>")
        for week in cal.monthdayscalendar(year, m):
            html.append("<tr>")
            for d in week:
                if d == 0:
                    html.append("<td></td>")
                    continue
                iso = f"{year:04d}-{m:02d}-{d:02d}"
                info = by_day.get(iso)
                cls = []
                if info:
                    cls.append("d-done" if info["done"] else "d-plan")
                if iso == today:
                    cls.append("d-today")
                cl = f' class="{" ".join(cls)}"' if cls else ""
                html.append(f"<td{cl}>{d}</td>")
            html.append("</tr>")
        html.append("</table></div>")
    html.append("</div>")
    st.markdown("\n".join(html), unsafe_allow_html=True)


def _sessions_by_day(items: list[dict]) -> dict[str, list[dict]]:
    m: dict[str, list[dict]] = {}
    for p in items:
        m.setdefault(p["date"], []).append(p)
    return m


def _render_month(year: int, month: int, by_day: dict[str, list[dict]]):
    """Vue mensuelle : grille avec le détail des séances dans chaque jour."""
    today = date.today().isoformat()
    css = """
    <style>
    .m-cal{width:100%;border-collapse:collapse;table-layout:fixed;}
    .m-cal th{padding:6px;font-size:12px;color:#888;border-bottom:1px solid #e0e0e0;}
    .m-cal td{height:96px;vertical-align:top;border:1px solid #eee;padding:4px;width:14.28%;}
    .m-other{background:#fafafa;}
    .m-day{font-size:12px;color:#666;font-weight:600;margin-bottom:3px;}
    .m-today{outline:2px solid #d32f2f;}
    .m-chip{display:block;font-size:10px;border-radius:4px;padding:2px 4px;margin-bottom:2px;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .c-done{background:#2e7d32;color:#fff;}
    .c-plan{background:#bbdefb;color:#0d47a1;}
    </style>
    """
    cal = _cal.Calendar(firstweekday=0)
    html = [css, '<table class="m-cal"><tr>']
    weekdays = [("Lundi", "L"), ("Mardi", "M"), ("Mercredi", "M"), ("Jeudi", "J"),
                ("Vendredi", "V"), ("Samedi", "S"), ("Dimanche", "D")]
    for full, abbr in weekdays:
        html.append(f'<th><span class="wd-full">{full}</span>'
                    f'<span class="wd-abbr">{abbr}</span></th>')
    html.append("</tr>")
    for week in cal.monthdatescalendar(year, month):
        html.append("<tr>")
        for d in week:
            iso = d.isoformat()
            cls = "m-other" if d.month != month else ""
            if iso == today:
                cls += " m-today"
            html.append(f'<td class="{cls.strip()}">')
            html.append(f'<div class="m-day">{d.day}</div>')
            for p in by_day.get(iso, []):
                c = "c-done" if p["done"] else "c-plan"
                emoji = _session_emoji(p)
                label = p.get("title") or p.get("session_type") or "Séance"
                html.append(f'<span class="m-chip {c}">{emoji} {label}</span>')
            html.append("</td>")
        html.append("</tr>")
    html.append("</table>")
    st.markdown("\n".join(html), unsafe_allow_html=True)


def _render_week(monday: date, by_day: dict[str, list[dict]]):
    """Vue semaine : 7 jours détaillés et éditables (lundi → dimanche)."""
    jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    today = date.today().isoformat()
    for i in range(7):
        d = monday + timedelta(days=i)
        iso = d.isoformat()
        head = f"**{jours[i]} {d.strftime('%d/%m')}**"
        if iso == today:
            head += " · 🔴 aujourd'hui"
        st.markdown(head)
        items = by_day.get(iso, [])
        if not items:
            st.caption("— repos / rien de prévu")
        for p in items:
            _render_session_row(p, key_prefix="wk", show_date=False)
        st.divider()


def _coach_about(p: dict):
    """Pré-remplit une question au coach sur cette séance et bascule sur le chat."""
    title = p.get("title") or p.get("session_type") or "séance"
    st.session_state["coach_prefill"] = (
        f"Parle-moi de ma séance prévue le {p['date']} : « {title} ». "
        "Pourquoi celle-ci à ce moment de ma progression, comment bien l'exécuter, "
        "et à quoi dois-je faire attention ?")
    st.session_state["_goto"] = "Coach IA"
    st.rerun()


def _render_session_row(p: dict, key_prefix: str, show_date: bool = True):
    """Ligne de séance : titre, détails (pourquoi + structure), discuter, fait, suppr."""
    emoji = _session_emoji(p)
    check = "✅" if p["done"] else "⬜"
    dur = f" · {p['duration_min']} min" if p.get("duration_min") else ""
    title = p.get("title") or p.get("session_type") or "Séance"
    when = f"**{p['date']}** · " if show_date else ""
    c1, c2, c3 = st.columns([8, 1, 1])
    with c1:
        st.markdown(f"{check} {when}{emoji} {title}{dur}")
        with st.expander("Pourquoi cette séance + structure"):
            if p.get("description"):
                st.markdown(f"_{p['description']}_")
            st.markdown("**🎯 Pourquoi cette séance**")
            st.write(p.get("rationale") or "— (regénère le plan avec l'IA pour le détail)")
            st.markdown("**🏗️ Structure**")
            st.write(p.get("structure") or "— (regénère le plan avec l'IA pour le détail)")
            if st.button("💬 En discuter avec le coach",
                         key=f"{key_prefix}_talk_{p['id']}"):
                _coach_about(p)
    if not p["done"] and c2.button("✓", key=f"{key_prefix}_done_{p['id']}",
                                   help="Marquer fait"):
        P.set_done(p["id"], True)
        st.rerun()
    if c3.button("🗑️", key=f"{key_prefix}_del_{p['id']}", help="Supprimer"):
        P.delete_planned(p["id"])
        st.rerun()


def page_calendar():
    st.title("📅 Calendrier")
    st.caption("Ton plan d'entraînement annuel. Demande à l'IA de le construire ou "
               "de le réajuster ; les séances importées passent automatiquement en ✅.")

    P.reconcile_with_activities()

    # --- Bloc planification IA ---
    with st.container(border=True):
        st.subheader("🤖 Générer / réajuster le plan")
        if not AI.get_api_key():
            st.warning("⚠️ Aucune clé API DeepSeek. Va dans **Réglages** pour en ajouter une.")
        instruction = st.text_area(
            "Que veux-tu planifier ?",
            placeholder="Ex : prépare-moi les 2 prochaines semaines en course à pied, "
                        "3 séances/semaine, avec une sortie longue le dimanche.\n"
                        "Ou : j'ai un trail de 30 km le 15 septembre, planifie ma prépa.",
            height=90)
        c1, c2 = st.columns([1, 3])
        if c1.button("✨ Générer le plan", type="primary", width="stretch"):
            if instruction.strip():
                with st.spinner("L'IA construit ton plan…"):
                    sessions, msg = AI.generate_plan(instruction, AI.gather_context())
                if sessions:
                    st.session_state["proposed_plan"] = sessions
                    st.session_state["proposed_msg"] = msg
                else:
                    st.error(msg)
            else:
                st.warning("Décris d'abord ce que tu veux planifier.")
        c2.caption("L'IA tient compte de ta forme, tes séances faites et tes objectifs. "
                   "Rien n'est appliqué avant que tu choisisses comment.")

        # --- Aperçu de la proposition + choix (ajouter / remplacer / annuler) ---
        proposed = st.session_state.get("proposed_plan")
        if proposed:
            st.divider()
            st.markdown(f"**Proposition de l'IA** — {st.session_state.get('proposed_msg', '')}")
            st.caption(f"{len(proposed)} séance(s) proposée(s) :")
            for s in sorted(proposed, key=lambda x: x.get("date", "")):
                emo = _session_emoji(s)
                label = s.get("title") or s.get("session_type") or s.get("sport") or "Séance"
                dur = f" · {s['duration_min']} min" if s.get("duration_min") else ""
                st.write(f"- {emo} **{s.get('date')}** — {label}{dur}")
            b1, b2, b3 = st.columns(3)
            if b1.button("➕ Ajouter / modifier", type="primary", width="stretch",
                         help="Ajoute les nouvelles séances et met à jour celles du même "
                              "jour+sport. Le reste du plan est conservé."):
                added, updated = P.merge_sessions(proposed)
                st.session_state.pop("proposed_plan", None)
                st.session_state.pop("proposed_msg", None)
                st.success(f"Plan fusionné : {added} ajoutée(s), {updated} modifiée(s).")
                st.rerun()
            if b2.button("♻️ Tout remplacer", width="stretch",
                         help="Efface les séances non réalisées à partir de la 1re date "
                              "proposée, puis insère le nouveau plan."):
                from_date = min(s["date"] for s in proposed)
                n = P.bulk_replace_future(proposed, from_date=from_date)
                st.session_state.pop("proposed_plan", None)
                st.session_state.pop("proposed_msg", None)
                st.success(f"Plan remplacé : {n} séance(s) à partir du {from_date}.")
                st.rerun()
            if b3.button("✖️ Annuler", width="stretch"):
                st.session_state.pop("proposed_plan", None)
                st.session_state.pop("proposed_msg", None)
                st.rerun()

    # --- Sélecteur de vue ---
    view = st.segmented_control("Vue", ["Année", "Mois", "Semaine"],
                                default="Mois", key="cal_view")
    st.caption("🟩 réalisée · 🟦 planifiée · contour rouge = aujourd'hui")
    today = date.today()

    if view == "Mois":
        c1, c2 = st.columns(2)
        year = c1.selectbox("Année", [today.year - 1, today.year, today.year + 1],
                            index=1, key="m_year")
        month = c2.selectbox("Mois", list(range(1, 13)),
                             index=today.month - 1,
                             format_func=lambda m: MONTHS_FR[m - 1], key="m_month")
        items = P.list_planned(start=f"{year}-{month:02d}-01",
                               end=f"{year}-{month:02d}-31")
        _render_month(year, month, _sessions_by_day(items))

    elif view == "Semaine":
        ref = st.date_input("Semaine du", value=today, format="DD/MM/YYYY",
                            key="w_ref")
        monday = ref - timedelta(days=ref.weekday())
        sunday = monday + timedelta(days=6)
        st.caption(f"Du {monday.strftime('%d/%m')} au {sunday.strftime('%d/%m/%Y')}")
        items = P.list_planned(start=monday.isoformat(), end=sunday.isoformat())
        _render_week(monday, _sessions_by_day(items))
        return  # la vue semaine est déjà détaillée et éditable

    else:  # Année
        year = st.selectbox("Année", [today.year - 1, today.year, today.year + 1],
                            index=1, key="y_year")
        plan_year = P.list_planned(start=f"{year}-01-01", end=f"{year}-12-31")
        by_day: dict[str, dict] = {}
        for p in plan_year:
            slot = by_day.setdefault(p["date"], {"done": False})
            if p["done"]:
                slot["done"] = True
        _render_year(year, by_day)

    # --- Prochaines séances éditables (vues Année & Mois) ---
    st.subheader("Prochaines séances")
    upcoming = P.list_planned(start=today.isoformat())
    if not upcoming:
        st.info("Aucune séance planifiée à venir. Utilise le bloc ci-dessus ou demande "
                "au **Coach IA** de te faire un plan.")
    else:
        for p in upcoming[:40]:
            _render_session_row(p, key_prefix="up", show_date=True)


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
st.sidebar.title("🏔️ Training Hub")

# Import par upload (fonctionne en local ET sur le cloud).
uploaded = st.sidebar.file_uploader(
    "📤 Importer des fichiers .fit", type=["fit"],
    accept_multiple_files=True, key="fit_uploader")
if uploaded and st.sidebar.button("Importer les fichiers sélectionnés",
                                  type="primary", width="stretch"):
    imp = dup = 0
    errs = []
    for uf in uploaded:
        with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tmp:
            tmp.write(uf.getbuffer())
            tmp_path = Path(tmp.name)
        status = ingest_file(tmp_path)
        tmp_path.unlink(missing_ok=True)
        if status == "imported":
            imp += 1
        elif status == "duplicate":
            dup += 1
        else:
            errs.append(f"{uf.name}: {status}")
    linked = P.reconcile_with_activities()
    msg = f"{imp} importée(s), {dup} doublon(s)"
    if linked:
        msg += f", {linked} du plan validée(s)"
    if errs:
        st.sidebar.error(msg + " | " + " ; ".join(errs))
    else:
        st.sidebar.success(msg)
    st.rerun()

# En local seulement : import depuis le dossier inbox/.
if not IS_PG and st.sidebar.button("⬇️ Importer depuis inbox/", width="stretch"):
    res = scan_inbox()
    linked = P.reconcile_with_activities()
    msg = f"{len(res['imported'])} importée(s), {len(res['duplicate'])} doublon(s)"
    if linked:
        msg += f", {linked} séance(s) du plan validée(s)"
    if res["error"]:
        st.sidebar.error(msg + f", {len(res['error'])} erreur(s): {res['error']}")
    else:
        st.sidebar.success(msg)

if "_goto" in st.session_state:
    st.session_state["nav"] = st.session_state.pop("_goto")
page = st.sidebar.radio("Navigation",
                        ["Vue d'ensemble", "Coach IA", "Calendrier", "Objectifs",
                         "Mémoire", "Analyse de séance", "Forme & charge",
                         "Records", "Progression", "Profil", "Réglages"],
                        key="nav")
if not IS_PG:
    st.sidebar.caption("Astuce : tu peux aussi déposer tes `.fit` dans `inbox/`.")

PAGES = {
    "Vue d'ensemble": page_overview,
    "Coach IA": page_coach,
    "Calendrier": page_calendar,
    "Objectifs": page_objectives,
    "Mémoire": page_memory,
    "Analyse de séance": page_session,
    "Forme & charge": page_form,
    "Records": page_records,
    "Progression": page_progression,
    "Profil": page_profile,
    "Réglages": page_settings,
}
PAGES[page]()
