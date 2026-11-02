"""
Carga data/llamadas.csv a la tabla `llamadas` en PostgreSQL.
Se corre por separado de load_data.py porque llamadas.csv se genera con un
script distinto (generate_calls.py) y depende de que customers/card_transactions
ya existan (por las FK).

Uso:
    source .venv/bin/activate
    python scripts/load_llamadas.py
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)  # permite importar claims_ai desde scripts/ o db/

from claims_ai.models import database  # noqa: E402

CSV_PATH = os.path.join(RAIZ, "data", "llamadas.csv")


def _connect():
    return database.connect(dict_rows=False)


def main():
    if not os.path.exists(CSV_PATH):
        print(f"No se encontro {CSV_PATH}. Corre primero: python scripts/generate_calls.py")
        return

    conn = _connect()
    print("Conexion a PostgreSQL correcta.\n")

    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE llamadas RESTART IDENTITY CASCADE;")

        with open(CSV_PATH, "r", encoding="utf-8") as f:
            with cur.copy(
                "COPY llamadas FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
            ) as copy:
                while data := f.read(8192):
                    copy.write(data)

        cur.execute(
            "SELECT setval(pg_get_serial_sequence('llamadas','call_id'), "
            "COALESCE((SELECT MAX(call_id) FROM llamadas), 1));"
        )

        cur.execute("SELECT COUNT(*) FROM llamadas;")
        print(f"llamadas: {cur.fetchone()[0]} filas cargadas")

        cur.execute(
            "SELECT modalidad_real, COUNT(*) FROM llamadas "
            "GROUP BY modalidad_real ORDER BY COUNT(*) DESC;"
        )
        print("\nDistribucion por modalidad:")
        for modalidad, n in cur.fetchall():
            print(f"  {modalidad}: {n}")

    conn.commit()
    conn.close()
    print("\nCarga completa.")


if __name__ == "__main__":
    main()