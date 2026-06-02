"""Base du Training Hub : profil, séances, échantillons.

Double backend :
- En local (sur le Mac) : SQLite (fichier data/hub.db) — comportement historique.
- En ligne (Streamlit Cloud) : Postgres si la variable d'env DATABASE_URL est
  définie (ou un secret Streamlit du même nom). Un wrapper imite l'API sqlite3
  utilisée par le reste du code (execute/executemany/executescript, rows en
  dict, context manager qui commit), avec traduction SQL à la volée.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = HUB_DIR / "data"
INBOX_DIR = HUB_DIR / "inbox"
ARCHIVE_DIR = INBOX_DIR / "_archive"
REPORTS_DIR = HUB_DIR / "reports"
DB_PATH = DATA_DIR / "hub.db"


def _database_url() -> str | None:
    """URL Postgres si on tourne en mode hébergé, sinon None (= SQLite local)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:  # sur Streamlit Cloud, la valeur arrive via les secrets
        import streamlit as st  # noqa: PLC0415
        if "DATABASE_URL" in st.secrets:
            return str(st.secrets["DATABASE_URL"])
    except Exception:
        pass
    return None


DATABASE_URL = _database_url()
IS_PG = bool(DATABASE_URL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    sexe          TEXT,
    age           INTEGER,
    poids_kg      REAL,
    taille_cm     REAL,
    fc_max        INTEGER,
    fc_repos      INTEGER,
    ftp_w         INTEGER,
    allure_seuil  INTEGER,            -- secondes par km
    objectif_poids_kg REAL,
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash       TEXT UNIQUE NOT NULL,
    file_name       TEXT,
    sport           TEXT,
    sub_sport       TEXT,
    start_time      TEXT,             -- ISO 8601 UTC
    duration_s      REAL,
    distance_m      REAL,
    elevation_gain_m REAL,
    avg_hr          INTEGER,
    max_hr          INTEGER,
    avg_cadence     REAL,
    avg_power       REAL,
    avg_speed       REAL,             -- m/s
    calories        INTEGER,
    imported_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS records (
    activity_id   INTEGER NOT NULL,
    elapsed_s     REAL,
    hr            INTEGER,
    speed         REAL,               -- m/s
    cadence       REAL,
    power         REAL,
    altitude      REAL,
    distance      REAL,               -- m cumulés
    temperature   REAL,
    FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS objectives (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    target_date TEXT,                -- ISO date, optionnel
    done        INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS planned_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,        -- ISO date (jour prévu)
    sport        TEXT,                 -- running / cycling / trail …
    session_type TEXT,                 -- footing, fractionné, seuil, sortie longue…
    title        TEXT,                 -- titre court
    description  TEXT,                 -- détail (durée, zones, allure, D+…)
    rationale    TEXT,                 -- POURQUOI cette séance (objectif physio)
    structure    TEXT,                 -- déroulé : échauffement / corps / récup
    duration_min INTEGER,              -- durée prévue (min)
    intensity    TEXT,                 -- facile / modéré / intense
    done         INTEGER DEFAULT 0,    -- réalisée ?
    activity_id  INTEGER,              -- vraie séance liée si réalisée
    created_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS coach_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL,        -- thème court, ex : "Cadence vélo"
    observation TEXT,                 -- constat chiffré observé sur l'historique
    advice      TEXT,                 -- conseil concret à appliquer à l'entraînement
    source      TEXT DEFAULT 'ia',    -- 'ia' (analyse auto) ou 'manuel'
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS coach_memory_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   INTEGER,              -- entrée d'origine (peut avoir été supprimée)
    topic       TEXT NOT NULL,
    observation TEXT,                 -- valeur AVANT le changement
    advice      TEXT,
    source      TEXT,
    archived_at TEXT DEFAULT (datetime('now'))  -- quand cette version a été remplacée
);

CREATE INDEX IF NOT EXISTS idx_records_activity ON records(activity_id);
CREATE INDEX IF NOT EXISTS idx_mem_history_topic ON coach_memory_history(topic);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_planned_date ON planned_sessions(date);
"""


# Schéma Postgres : mêmes tables, dialecte adapté (SERIAL, now()::text…).
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS profile (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    sexe          TEXT,
    age           INTEGER,
    poids_kg      DOUBLE PRECISION,
    taille_cm     DOUBLE PRECISION,
    fc_max        INTEGER,
    fc_repos      INTEGER,
    ftp_w         INTEGER,
    allure_seuil  INTEGER,
    objectif_poids_kg DOUBLE PRECISION,
    updated_at    TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS activities (
    id              SERIAL PRIMARY KEY,
    file_hash       TEXT UNIQUE NOT NULL,
    file_name       TEXT,
    sport           TEXT,
    sub_sport       TEXT,
    start_time      TEXT,
    duration_s      DOUBLE PRECISION,
    distance_m      DOUBLE PRECISION,
    elevation_gain_m DOUBLE PRECISION,
    avg_hr          INTEGER,
    max_hr          INTEGER,
    avg_cadence     DOUBLE PRECISION,
    avg_power       DOUBLE PRECISION,
    avg_speed       DOUBLE PRECISION,
    calories        INTEGER,
    imported_at     TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS records (
    activity_id   INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    elapsed_s     DOUBLE PRECISION,
    hr            INTEGER,
    speed         DOUBLE PRECISION,
    cadence       DOUBLE PRECISION,
    power         DOUBLE PRECISION,
    altitude      DOUBLE PRECISION,
    distance      DOUBLE PRECISION,
    temperature   DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS objectives (
    id          SERIAL PRIMARY KEY,
    text        TEXT NOT NULL,
    target_date TEXT,
    done        INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS planned_sessions (
    id           SERIAL PRIMARY KEY,
    date         TEXT NOT NULL,
    sport        TEXT,
    session_type TEXT,
    title        TEXT,
    description  TEXT,
    rationale    TEXT,
    structure    TEXT,
    duration_min INTEGER,
    intensity    TEXT,
    done         INTEGER DEFAULT 0,
    activity_id  INTEGER REFERENCES activities(id) ON DELETE SET NULL,
    created_at   TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS coach_memory (
    id          SERIAL PRIMARY KEY,
    topic       TEXT NOT NULL,
    observation TEXT,
    advice      TEXT,
    source      TEXT DEFAULT 'ia',
    created_at  TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS')),
    updated_at  TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS coach_memory_history (
    id          SERIAL PRIMARY KEY,
    memory_id   INTEGER,
    topic       TEXT NOT NULL,
    observation TEXT,
    advice      TEXT,
    source      TEXT,
    archived_at TEXT DEFAULT (to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_records_activity ON records(activity_id);
CREATE INDEX IF NOT EXISTS idx_mem_history_topic ON coach_memory_history(topic);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_planned_date ON planned_sessions(date);
"""


def _translate(sql: str) -> str:
    """Traduit le SQL SQLite vers Postgres (placeholders et quelques fonctions)."""
    sql = sql.replace("?", "%s")
    sql = sql.replace("datetime('now')", "to_char(now(),'YYYY-MM-DD HH24:MI:SS')")
    # IFNULL(a, b) -> COALESCE(a, b) (insensible à la casse)
    sql = sql.replace("IFNULL(", "COALESCE(").replace("ifnull(", "COALESCE(")
    return sql


class _PgCursor:
    """Curseur Postgres exposant l'API minimale utilisée par le code (fetchone,
    fetchall, itération, lastrowid)."""

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    @property
    def description(self):
        return self._cur.description

    def fetchone(self):
        try:
            return self._cur.fetchone()
        except Exception:
            return None

    def fetchall(self):
        try:
            return self._cur.fetchall()
        except Exception:
            return []

    def __iter__(self):
        return iter(self.fetchall())


class _PgConnection:
    """Imite l'API sqlite3 utilisée dans le projet, par-dessus psycopg2.

    Une nouvelle connexion est ouverte à chaque get_connection(), et fermée à
    la sortie du `with` (commit si succès, rollback sinon)."""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415
        import psycopg2.extras  # noqa: PLC0415
        self._extras = psycopg2.extras
        # Connexion DIRECTE (non poolée) : on retire "-pooler" du host. Le pooler
        # (pgbouncer) gardait des transactions « idle » côté serveur quand un
        # upload était interrompu → verrous bloqués et écran figé. La connexion
        # directe se ferme net à la déconnexion et autorise statement_timeout.
        dsn = dsn.replace("-pooler.", ".")
        self._conn = psycopg2.connect(
            dsn, connect_timeout=10,
            options="-c statement_timeout=60000",  # 60 s max par requête
        )

    def _new_cursor(self):
        return self._conn.cursor(cursor_factory=self._extras.RealDictCursor)

    def execute(self, sql: str, params=()):
        cur = self._new_cursor()
        cur.execute(_translate(sql), tuple(params) if params else None)
        return _PgCursor(cur)

    def executemany(self, sql: str, seq):
        # execute_batch regroupe les lignes en quelques aller-retours réseau
        # (au lieu d'un par ligne) : insérer les ~8000 points d'un .fit passe
        # ainsi de plusieurs minutes à quelques secondes.
        cur = self._new_cursor()
        self._extras.execute_batch(
            cur, _translate(sql), [tuple(p) for p in seq], page_size=1000)
        return _PgCursor(cur)

    def executescript(self, script: str):
        cur = self._conn.cursor()
        cur.execute(script)  # psycopg2 accepte plusieurs requêtes séparées par ;
        return _PgCursor(cur)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


def get_connection():
    if IS_PG:
        return _PgConnection(DATABASE_URL)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def insert_returning_id(conn, sql: str, params) -> int:
    """INSERT renvoyant l'id de la ligne créée, quel que soit le backend."""
    if IS_PG:
        cur = conn.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
        row = cur.fetchone()
        return int(row["id"]) if row else 0
    return conn.execute(sql, params).lastrowid


def read_df(conn, sql: str, params=()):
    """Lecture vers un DataFrame pandas, backend-agnostique (remplace read_sql)."""
    import pandas as pd  # noqa: PLC0415
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    if rows:
        return pd.DataFrame([dict(r) for r in rows])
    # Résultat vide : on garde quand même les noms de colonnes.
    cols = [d[0] for d in (cur.description or [])]
    return pd.DataFrame(columns=cols)


def _migrate(conn) -> None:
    """Ajoute les colonnes manquantes aux bases SQLite déjà créées."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(planned_sessions)")}
    for col in ("rationale", "structure"):
        if col not in cols:
            conn.execute(f"ALTER TABLE planned_sessions ADD COLUMN {col} TEXT")


def init_db() -> None:
    if IS_PG:
        with get_connection() as conn:
            conn.executescript(SCHEMA_PG)
        return
    for d in (DATA_DIR, INBOX_DIR, ARCHIVE_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


if __name__ == "__main__":
    init_db()
    print(f"Base initialisée ({'Postgres' if IS_PG else DB_PATH})")
