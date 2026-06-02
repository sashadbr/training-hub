"""Copie les données SQLite locales -> Postgres (Neon) pour l'hébergement.

À lancer UNE fois, depuis le Mac, vers une base Postgres de préférence vide :

    .venv/bin/python app/migrate_to_pg.py 'postgresql://user:pass@host/db?sslmode=require'

ou en passant l'URL par l'environnement :

    DATABASE_URL='postgresql://...' .venv/bin/python app/migrate_to_pg.py

Le script crée le schéma sur Postgres, copie chaque table en préservant les id
(grâce à ON CONFLICT DO NOTHING, il est rejouable sans doublon), puis recale
les séquences SERIAL pour que les futurs INSERT repartent du bon numéro.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

# Ordre respectant les clés étrangères (activities avant records/planned_sessions).
TABLES = ["profile", "activities", "records", "objectives",
          "planned_sessions", "coach_memory", "coach_memory_history"]
SERIAL_TABLES = ["activities", "objectives", "planned_sessions",
                 "coach_memory", "coach_memory_history"]


def main() -> None:
    dsn = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Donne l'URL Postgres en argument ou via DATABASE_URL.")
    if not db.DB_PATH.exists():
        sys.exit(f"Base SQLite introuvable : {db.DB_PATH}")

    import psycopg2  # noqa: PLC0415

    sl = sqlite3.connect(db.DB_PATH)
    sl.row_factory = sqlite3.Row
    pg = psycopg2.connect(dsn)
    cur = pg.cursor()

    cur.execute(db.SCHEMA_PG)
    pg.commit()
    print("Schéma Postgres créé / vérifié.")

    total = 0
    for t in TABLES:
        cols = [r[1] for r in sl.execute(f"PRAGMA table_info({t})")]
        if not cols:
            print(f"  {t}: table absente côté SQLite, ignorée")
            continue
        rows = sl.execute(f"SELECT {', '.join(cols)} FROM {t}").fetchall()
        if not rows:
            print(f"  {t}: 0")
            continue
        ph = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({ph}) "
               f"ON CONFLICT DO NOTHING")
        cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        pg.commit()
        print(f"  {t}: {len(rows)} ligne(s) copiée(s)")
        total += len(rows)

    for t in SERIAL_TABLES:
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {t}), 1))")
    pg.commit()
    print(f"Terminé : {total} ligne(s) au total. Séquences recalées.")

    sl.close()
    pg.close()


if __name__ == "__main__":
    main()
