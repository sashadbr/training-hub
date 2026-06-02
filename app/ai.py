"""Coach IA via DeepSeek (API compatible OpenAI).

- Assemble un contexte athlète (profil, forme, séances, objectifs).
- Dialogue en streaming avec un coach pédagogue (trail / course / vélo).
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import requests

import re
from datetime import timedelta

import load as L
from db import get_connection, read_df
from memory_store import list_memory
from metrics import _num, format_duration, format_pace, pace_sec_per_km
from objectives_store import list_objectives
from plan_store import list_planned
from profile_store import get_profile
from secrets_store import get_api_key, load_secrets

COACH_SYSTEM = """Tu es le coach sportif personnel de l'athlète, spécialisé en trail, \
course à pied et vélo de route. Tu réponds en français, sur un ton pédagogue : tu \
expliques toujours le POURQUOI de tes recommandations (objectif physiologique, \
gestion de la charge, progressivité).

Principes :
- Objectif de l'athlète : progression continue et régulière, sans surentraînement.
- Appuie-toi sur les données fournies (profil, forme TSB, dernières séances, objectifs).
- La forme (TSB) guide l'intensité : TSB très négatif = privilégier récup/facile ; \
TSB positif = possibilité d'une séance clé ou d'un test.
- Le REPOS fait partie de l'entraînement : n'hésite jamais à recommander un jour de \
repos ou de récupération quand la fatigue est élevée (TSB bas, charge importante, \
ressenti). La progression vient aussi de la récupération ; sois clair et assumé là-dessus.
- Propose des séances CONCRÈTES : type (footing, fractionné, seuil, sortie longue, \
côtes, récup…), durée, zones de FC (Z1–Z5), allure ou puissance/cadence cible quand \
c'est pertinent, et D+ pour le trail.
- Quand on te demande un plan ou une projection sur plusieurs jours/semaines/mois, \
structure clairement (par semaine, avec jours de repos), et explique la logique de \
progression et les phases.
- Tiens compte de ce que l'athlète te dit (séances déjà faites, ressenti, contraintes, \
type d'entraînement souhaité) et ajuste.
- Reste prudent : tu n'es pas médecin ; en cas de douleur/blessure, conseille la prudence \
et le repos. Rappelle d'adapter selon le ressenti.
- Sois clair et structuré (listes, gras), mais sans bla-bla inutile.

MÉMOIRE PERSONNELLE : tu disposes d'une mémoire des points faibles et habitudes de \
l'athlète (voir « # Mémoire coach »). Utilise-la SYSTÉMATIQUEMENT pour personnaliser tes \
conseils (ex. s'il gère mal sa cadence à vélo, rappelle-le-lui et intègre du travail de \
cadence). Quand tu constates un nouveau point faible récurrent, ou que l'athlète t'en \
signale un, mémorise-le en terminant ta réponse par un bloc ```memory contenant du JSON :
```memory
{"insights": [{"topic": "Cadence vélo", "observation": "cadence moyenne basse (~78 rpm) et irrégulière sur les 6 derniers mois", "advice": "viser 85-90 rpm, intégrer des éducatifs de cadence sur les footings vélo"}]}
```
N'ajoute ce bloc QUE quand il y a vraiment un point à enregistrer/mettre à jour.

CALENDRIER : l'athlète a un calendrier de séances planifiées (voir « # Plan en cours »). \
Quand il te demande de PLANIFIER, MODIFIER ou RÉAJUSTER son calendrier (ajouter des séances, \
décaler, adapter après une séance faite…), explique d'abord ta logique en français, PUIS \
termine ta réponse par un bloc de code ```plan contenant UNIQUEMENT du JSON de la forme :
```plan
{"sessions": [
  {"date": "2026-06-03", "sport": "running", "session_type": "footing",
   "title": "Footing Z2", "description": "45 min en Z2, allure facile",
   "rationale": "Séance de récup active : entretient l'endurance de base sans ajouter de fatigue, idéale vu ton TSB négatif.",
   "structure": "10 min échauffement progressif Z1→Z2 · 30 min en Z2 (allure facile, FC 130-145) · 5 min retour au calme + étirements",
   "duration_min": 45, "intensity": "facile"}
]}
```
Règles du bloc plan : dates ISO (AAAA-MM-JJ) ; sport ∈ running/cycling/trail ; \
n'inclus que les séances FUTURES à (ré)écrire ; ce bloc remplacera les séances non \
réalisées à partir de la 1re date du bloc. N'ajoute le bloc QUE si on te demande de \
toucher au calendrier."""


def _load_activities() -> pd.DataFrame:
    with get_connection() as conn:
        df = read_df(conn, "SELECT * FROM activities ORDER BY start_time DESC")
    if not df.empty:
        df["start_time"] = pd.to_datetime(df["start_time"], utc=True, format="ISO8601")
    return df


def gather_context() -> str:
    """Construit le bloc de contexte athlète injecté au coach."""
    prof = get_profile()
    df = _load_activities()
    lines = [f"Date du jour : {date.today().isoformat()}", ""]

    # Profil
    lines.append("# Profil")
    if prof:
        allure = prof.get("allure_seuil")
        lines.append(
            f"- Sexe {prof.get('sexe')}, {prof.get('age')} ans, "
            f"{prof.get('poids_kg')} kg, {prof.get('taille_cm')} cm")
        lines.append(
            f"- FC max {prof.get('fc_max')}, FC repos {prof.get('fc_repos')}, "
            f"FTP vélo {prof.get('ftp_w')} W, "
            f"allure seuil {format_pace(allure) if allure else '—'}")
        if prof.get("objectif_poids_kg"):
            lines.append(f"- Objectif de poids : {prof.get('objectif_poids_kg')} kg")
    else:
        lines.append("- (profil non renseigné)")

    # Forme
    lines.append("\n# Forme actuelle (modèle de charge FC)")
    fs = L.fitness_series(df, prof)
    if not fs.empty:
        last = fs.iloc[-1]
        lines.append(
            f"- Fitness CTL {last['ctl']:.0f}, Fatigue ATL {last['atl']:.0f}, "
            f"Forme TSB {last['tsb']:+.0f} → {L.form_comment(last['tsb'])}")
        # Charge des 4 dernières semaines
        fs2 = fs.copy()
        fs2["date"] = pd.to_datetime(fs2["date"])
        weekly = fs2.set_index("date")["load"].resample("W-MON").sum().tail(4)
        wk = ", ".join(f"{d.strftime('%d/%m')}: {v:.0f}" for d, v in weekly.items())
        lines.append(f"- Charge hebdo (TRIMP) récente : {wk}")
    else:
        lines.append("- (pas assez de données)")

    # Dernières séances
    lines.append("\n# Dernières séances (10 max)")
    if df.empty:
        lines.append("- (aucune séance enregistrée)")
    else:
        dft = L.add_trimp(df, prof).head(10)
        for _, r in dft.iterrows():
            when = r["start_time"].strftime("%d/%m")
            km = (_num(r["distance_m"]) or 0) / 1000
            dur = format_duration(r["duration_s"])
            dplus = _num(r["elevation_gain_m"]) or 0
            hr = _num(r["avg_hr"])
            spd = _num(r["avg_speed"])
            if r["sport"] == "cycling":
                intens = f"{spd * 3.6:.1f} km/h" if spd else "—"
            else:
                intens = format_pace(pace_sec_per_km(spd))
            lines.append(
                f"- {when} {r['sport']}/{r['sub_sport'] or '-'} : {km:.1f} km, "
                f"{dur}, D+{dplus:.0f} m, FC {hr or '—'}, {intens}, "
                f"charge {r['trimp']:.0f}")

    # Objectifs
    lines.append("\n# Objectifs")
    objs = list_objectives(include_done=False)
    if objs:
        for o in objs:
            ech = f" (échéance {o['target_date']})" if o["target_date"] else ""
            lines.append(f"- {o['text']}{ech}")
    else:
        lines.append("- (aucun objectif défini)")

    # Mémoire coach : points faibles / patterns à surveiller
    lines.append("\n# Mémoire coach (points personnels à surveiller)")
    mem = list_memory()
    if mem:
        for m in mem:
            obs = f" — {m['observation']}" if m.get("observation") else ""
            adv = f" → conseil : {m['advice']}" if m.get("advice") else ""
            lines.append(f"- {m['topic']}{obs}{adv}")
    else:
        lines.append("- (aucun point mémorisé pour l'instant)")

    # Plan en cours (séances planifiées à venir)
    lines.append("\n# Plan en cours (séances planifiées, 4 semaines à venir)")
    today = date.today()
    horizon = (today + timedelta(days=28)).isoformat()
    plan = list_planned(start=today.isoformat(), end=horizon)
    if plan:
        for p in plan:
            etat = "✓ fait" if p["done"] else "à faire"
            dur = f", {p['duration_min']} min" if p.get("duration_min") else ""
            lines.append(
                f"- {p['date']} [{etat}] {p.get('sport') or '-'}/"
                f"{p.get('session_type') or '-'} : {p.get('title') or ''}{dur}")
    else:
        lines.append("- (aucune séance planifiée)")

    return "\n".join(lines)


def build_messages(history: list[dict], context: str) -> list[dict]:
    """history = [{'role':'user'/'assistant','content':...}, ...]."""
    sys = COACH_SYSTEM + "\n\n=== DONNÉES DE L'ATHLÈTE ===\n" + context
    return [{"role": "system", "content": sys}, *history]


def stream_chat(history: list[dict], context: str):
    """Générateur de morceaux de texte depuis DeepSeek (SSE streaming)."""
    key = get_api_key()
    if not key:
        yield ("⚠️ Aucune clé API DeepSeek. Va dans **Réglages** pour en ajouter une.")
        return
    s = load_secrets()
    url = s.get("deepseek_base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    payload = {
        "model": s.get("deepseek_model", "deepseek-chat"),
        "messages": build_messages(history, context),
        "stream": True,
        "temperature": 0.6,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        with requests.post(url, headers=headers, json=payload, stream=True,
                           timeout=120) as resp:
            if resp.status_code != 200:
                yield f"⚠️ Erreur API DeepSeek ({resp.status_code}) : {resp.text[:300]}"
                return
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except requests.RequestException as e:
        yield f"⚠️ Connexion à DeepSeek impossible : {e}"


PLAN_SCHEMA_HINT = (
    'Réponds UNIQUEMENT avec un objet JSON de la forme '
    '{"sessions": [{"date": "AAAA-MM-JJ", "sport": "running|cycling|trail", '
    '"session_type": "footing|fractionné|seuil|sortie longue|côtes|récup|repos", '
    '"title": "titre court", '
    '"description": "résumé en 1 phrase", '
    '"rationale": "POURQUOI cette séance ce jour-là : objectif physiologique, '
    'place dans la progression, lien avec la forme/charge et les objectifs", '
    '"structure": "déroulé détaillé : échauffement (durée+zone), corps de séance '
    '(répétitions, durées, zones FC, allure/puissance cible, récup entre répétitions), '
    'retour au calme", '
    '"duration_min": entier, "intensity": "facile|modéré|intense"}]}. '
    'Toutes les dates doivent être futures. Pas de texte hors du JSON.'
)

PLAN_DIRECTIVE = (
    "Avant de planifier, ANALYSE attentivement :\n"
    "- les DERNIÈRES SÉANCES (type, charge, FC, récence) et la charge récente "
    "(CTL/ATL/TSB) : ne programme pas deux séances intenses d'affilée, place du repos "
    "ou du facile après une grosse charge ou quand le TSB est très négatif ;\n"
    "- la MÉMOIRE COACH (points faibles) : intègre concrètement du travail dessus "
    "(ex. si la cadence vélo est basse, ajoute des éducatifs de cadence) et rappelle-le "
    "dans le champ rationale ;\n"
    "- les OBJECTIFS de l'athlète : oriente la progression vers eux.\n"
    "PLACE chaque séance le JOUR le plus pertinent (espacement optimal, alternance "
    "intensité/récup, sorties longues plutôt le week-end), pas mécaniquement. "
    "Adapte le volume et la difficulté à l'historique RÉEL de l'athlète, "
    "et explique systématiquement le POURQUOI du placement dans le champ rationale.\n"
    "REPOS : n'hésite JAMAIS à programmer des jours de REPOS ou de récupération "
    "(session_type = \"repos\" ou \"récup\"). Inclus-les EXPLICITEMENT dans le plan "
    "comme des séances à part entière, avec leur propre rationale (pourquoi se reposer "
    "ce jour-là : assimilation, TSB trop bas, charge élevée, prévention blessure). "
    "Mieux vaut un jour de repos qu'une séance de trop : la progression vient aussi "
    "de la récupération. Vise en général 1 à 2 jours de repos par semaine, davantage "
    "si la fatigue (ATL/TSB) le justifie."
)


def generate_plan(instruction: str, context: str) -> tuple[list[dict], str]:
    """Demande à DeepSeek un plan structuré (JSON). Retourne (sessions, message).

    `instruction` = consigne de l'athlète (horizon, objectif, type voulu…).
    En cas d'erreur, sessions == [] et message décrit le problème.
    """
    key = get_api_key()
    if not key:
        return [], "⚠️ Aucune clé API DeepSeek. Va dans Réglages pour en ajouter une."
    s = load_secrets()
    url = s.get("deepseek_base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    sys = (COACH_SYSTEM + "\n\n=== DONNÉES DE L'ATHLÈTE ===\n" + context
           + "\n\n=== STATISTIQUES DE L'HISTORIQUE ===\n" + build_stats_summary()
           + "\n\n=== DIRECTIVES DE PLANIFICATION ===\n" + PLAN_DIRECTIVE
           + "\n\n=== FORMAT DE RÉPONSE ===\n" + PLAN_SCHEMA_HINT)
    payload = {
        "model": s.get("deepseek_model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": instruction},
        ],
        "stream": False,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
    except requests.RequestException as e:
        return [], f"⚠️ Connexion à DeepSeek impossible : {e}"
    if resp.status_code != 200:
        return [], f"⚠️ Erreur API DeepSeek ({resp.status_code}) : {resp.text[:300]}"
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        sessions = _parse_sessions(content)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return [], f"⚠️ Réponse IA illisible : {e}"
    if not sessions:
        return [], "⚠️ L'IA n'a renvoyé aucune séance exploitable."
    return sessions, f"✅ {len(sessions)} séance(s) planifiée(s)."


_FENCE_RE = re.compile(r"```(?:[a-zA-Z]+)?\s*(\{.*?\})\s*```", re.DOTALL)


def _all_json_blocks(text: str) -> list[dict]:
    out = []
    for m in _FENCE_RE.finditer(text or ""):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def extract_plan_block(text: str) -> list[dict]:
    """Extrait les séances d'un éventuel bloc ```plan JSON dans une réponse de chat."""
    for data in _all_json_blocks(text):
        if isinstance(data, dict) and "sessions" in data:
            return _parse_sessions(json.dumps(data))
    return []


def extract_memory_block(text: str) -> list[dict]:
    """Extrait les insights d'un éventuel bloc ```memory JSON dans une réponse de chat."""
    for data in _all_json_blocks(text):
        if isinstance(data, dict) and "insights" in data:
            return _parse_insights(data.get("insights"))
    return []


def strip_blocks(text: str) -> str:
    """Retire les blocs JSON (plan/memory) du texte affiché à l'utilisateur."""
    return _FENCE_RE.sub("", text or "").strip()


def _parse_sessions(raw: str) -> list[dict]:
    """Parse un JSON {"sessions": [...]} (ou une liste brute) en liste de dicts propres."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data.get("sessions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    keep = ("date", "sport", "session_type", "title", "description",
            "rationale", "structure", "duration_min", "intensity")
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("date"):
            continue
        sess = {k: it.get(k) for k in keep}
        dm = sess.get("duration_min")
        try:
            sess["duration_min"] = int(dm) if dm not in (None, "") else None
        except (TypeError, ValueError):
            sess["duration_min"] = None
        out.append(sess)
    return out


def _parse_insights(items) -> list[dict]:
    """Nettoie une liste d'insights {topic, observation, advice}."""
    out = []
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, dict) and it.get("topic"):
            out.append({"topic": it.get("topic"),
                        "observation": it.get("observation"),
                        "advice": it.get("advice")})
    return out


def build_stats_summary() -> str:
    """Résumé statistique de l'historique, par sport, pour l'analyse des points faibles."""
    df = _load_activities()
    if df.empty:
        return "(aucune séance enregistrée)"
    lines = []
    for sport, g in df.groupby("sport"):
        n = len(g)
        d0 = g["start_time"].min().strftime("%d/%m/%Y")
        d1 = g["start_time"].max().strftime("%d/%m/%Y")
        km = (g["distance_m"].fillna(0) / 1000).mean()
        cad = _num(g["avg_cadence"].mean())
        cad_std = _num(g["avg_cadence"].std())
        hr = _num(g["avg_hr"].mean())
        spd = _num(g["avg_speed"].mean())
        dplus = _num(g["elevation_gain_m"].fillna(0).mean()) or 0
        if sport == "cycling" and spd:
            intens = f"{spd * 3.6:.1f} km/h"
        elif spd:
            intens = format_pace(pace_sec_per_km(spd))
        else:
            intens = "—"
        lines.append(f"## {sport} — {n} séances ({d0} → {d1})")
        lines.append(f"- Distance moy {km:.1f} km · D+ moy {dplus:.0f} m · "
                     f"FC moy {hr:.0f} · allure/vitesse moy {intens}"
                     if hr else
                     f"- Distance moy {km:.1f} km · D+ moy {dplus:.0f} m · "
                     f"FC moy — · allure/vitesse moy {intens}")
        if cad:
            lines.append(f"- Cadence moy {cad:.0f} (variabilité ±{cad_std or 0:.0f})")
        else:
            lines.append("- Cadence : non disponible")

    # Tendance de cadence vélo, mois par mois
    bikes = df[df["sport"] == "cycling"].copy()
    if not bikes.empty and bikes["avg_cadence"].notna().any():
        bikes = bikes.set_index("start_time").sort_index()
        monthly = bikes["avg_cadence"].resample("MS").mean().dropna().tail(6)
        if not monthly.empty:
            tr = ", ".join(f"{d.strftime('%m/%Y')}: {v:.0f}" for d, v in monthly.items())
            lines.append(f"\n## Tendance cadence vélo (rpm/mois)\n- {tr}")
    return "\n".join(lines)


ANALYZE_HINT = (
    "Analyse l'historique et identifie les POINTS FAIBLES ou habitudes récurrentes "
    "de l'athlète (ex : cadence vélo basse/irrégulière, dérive cardiaque élevée, "
    "gestion d'allure, manque de D+, déséquilibre entre les sports, etc.). "
    'Réponds UNIQUEMENT en JSON {"insights": [{"topic": "thème court", '
    '"observation": "constat CHIFFRÉ basé sur les données fournies", '
    '"advice": "conseil concret à appliquer à l\'entraînement"}]}. '
    "3 à 6 insights maximum, les plus pertinents. Pas de texte hors du JSON."
)


def analyze_sessions(instruction: str = "") -> tuple[list[dict], str]:
    """Demande à l'IA d'analyser l'historique et de produire des insights (points faibles).

    Retourne (insights, message). insights = [{topic, observation, advice}, ...].
    """
    key = get_api_key()
    if not key:
        return [], "⚠️ Aucune clé API DeepSeek. Va dans Réglages pour en ajouter une."
    stats = build_stats_summary()
    if stats.startswith("(aucune"):
        return [], "⚠️ Aucune séance à analyser. Importe d'abord des fichiers .fit."
    s = load_secrets()
    url = s.get("deepseek_base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    sys = (COACH_SYSTEM + "\n\n=== DONNÉES DE L'ATHLÈTE ===\n" + gather_context()
           + "\n\n=== STATISTIQUES DE L'HISTORIQUE ===\n" + stats
           + "\n\n=== CONSIGNE D'ANALYSE ===\n" + ANALYZE_HINT)
    user = instruction.strip() or "Analyse mes séances et mets à jour ma mémoire coach."
    payload = {
        "model": s.get("deepseek_model", "deepseek-chat"),
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": user}],
        "stream": False,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
    except requests.RequestException as e:
        return [], f"⚠️ Connexion à DeepSeek impossible : {e}"
    if resp.status_code != 200:
        return [], f"⚠️ Erreur API DeepSeek ({resp.status_code}) : {resp.text[:300]}"
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        insights = _parse_insights(json.loads(content).get("insights"))
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
        return [], f"⚠️ Réponse IA illisible : {e}"
    if not insights:
        return [], "⚠️ L'IA n'a identifié aucun point particulier."
    return insights, f"✅ {len(insights)} point(s) analysé(s)."
