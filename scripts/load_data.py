"""
Carga los CSVs de ./data/ a superbank_db en PostgreSQL.
Respeta el orden de dependencias (FK) y usa COPY para velocidad.

Uso:
    source .venv/bin/activate
    python scripts/load_data.py
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)  # permite importar claims_ai desde scripts/ o db/

from claims_ai.models import database  # noqa: E402

DATA_DIR = os.path.join(RAIZ, "data")

# Orden importante: tablas padre antes que tablas hijas (por las FK)
TABLES_IN_ORDER = [
    "branches",
    "employees",
    "customers",
    "accounts",
    "cards",
    "card_transactions",
    "transactions",
    "loans",
    "loan_payments",
    "support_tickets",
]

# Nombre de la columna id (SERIAL/PK) por tabla, para reajustar la secuencia tras el COPY
ID_COLUMN = {
    "branches": "branch_id",
    "employees": "employee_id",
    "customers": "customer_id",
    "accounts": "account_id",
    "cards": "card_id",
    "card_transactions": "card_txn_id",
    "transactions": "transaction_id",
    "loans": "loan_id",
    "loan_payments": "payment_id",
    "support_tickets": "ticket_id",
}


def main():
    conn = database.connect(dict_rows=False)
    print("Conexion a PostgreSQL correcta.\n")

    with conn.cursor() as cur:
        for table in TABLES_IN_ORDER:
            csv_path = os.path.join(DATA_DIR, f"{table}.csv")
            if not os.path.exists(csv_path):
                print(f"  [SKIP] No se encontro {csv_path}")
                continue

            # Vaciar la tabla antes de recargar (evita duplicados si se corre 2 veces)
            cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")

            with open(csv_path, "r", encoding="utf-8") as f:
                with cur.copy(
                    f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                ) as copy:
                    while data := f.read(8192):
                        copy.write(data)

            cur.execute(f"SELECT COUNT(*) FROM {table};")
            count = cur.fetchone()[0]
            print(f"  {table}: {count} filas cargadas")

            # Reajustar la secuencia del SERIAL al maximo id existente
            # (el CSV trae ids explicitos, asi que la secuencia interna se queda atras)
            id_col = ID_COLUMN[table]
            cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', '{id_col}'),
                    COALESCE((SELECT MAX({id_col}) FROM {table}), 1)
                );
            """)

    conn.commit()
    conn.close()
    print("\nCarga completa.")


if __name__ == "__main__":
    main()
