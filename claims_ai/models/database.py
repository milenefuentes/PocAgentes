"""
Conexión única a PostgreSQL. Los parámetros vienen del archivo .env
(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
y opcionalmente POSTGRES_SSLMODE, necesario para Neon).
"""
import os

import psycopg
from psycopg.rows import dict_row

from claims_ai import config  # noqa: F401  (carga el .env)


def connect(dict_rows: bool = True):
    """Abre una conexión. Con dict_rows=True cada fila se devuelve como diccionario."""
    kwargs = dict(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "superbank_db"),
        user=os.getenv("POSTGRES_USER", "agent"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )
    if dict_rows:
        kwargs["row_factory"] = dict_row
    sslmode = os.getenv("POSTGRES_SSLMODE")
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg.connect(**kwargs)
