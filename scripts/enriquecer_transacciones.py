"""
Enriquecimiento de card_transactions (Claims AI v3.2)

Completa el contexto transaccional que el Manual de Modalidades necesita y que los
datos sinteticos originales no traian: canal (POS / INT / APP), modo de entrada
(chip, contactless, banda, fallback, no presente, app), terminal o comercio online,
y ciudad del comercio.

No modifica montos, fechas ni comercios: esos datos aparecen textualmente en las
transcripciones de las llamadas. Solo completa filas con canal vacio.

Para los reclamos, el contexto es coherente con la modalidad real de la llamada
(p. ej. la clonacion ocurre en POS con banda magnetica en otra ciudad; el phishing,
por internet o app). Ademas planta dos patrones conocidos, marcados con
es_plantado = TRUE, para comprobar si el Agente 2 los detecta:

  1. Punto de compromiso: un grifo en Cusco (POS-CUS-COM-99) donde la mayoria de las
     victimas de tarjeta clonada compro dias antes del fraude, junto con clientes
     que nunca sufrieron fraude.
  2. Destino comun: una pasarela de pagos (PASARELA-PAY-01) por donde se cobra buena
     parte del fraude digital (phishing, SIM swap, toma de cuenta).

Es idempotente: si se vuelve a ejecutar, no toca filas ya completadas ni planta dos veces.
Para deshacerlo, restaurar el respaldo de backups/.

Uso:
    source .venv/bin/activate
    python db/migrate_db.py                      # crea las columnas (una vez)
    python scripts/enriquecer_transacciones.py
"""
import os
import random
import sys
import unicodedata
from datetime import timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)  # permite importar claims_ai desde scripts/ o db/

from claims_ai.models.database import connect as _connect  # noqa: E402

SEMILLA = 42
CIUDADES_PERU = ["Lima", "Arequipa", "Cusco", "Trujillo", "Chiclayo", "Piura", "Iquitos",
                 "Huancayo", "Tacna", "Puno", "Ica", "Chimbote"]
TERMINALES_POS_POR_CIUDAD_RUBRO = 4
COMERCIOS_WEB_POR_RUBRO = 8

# Probabilidad de que una compra legitima del rubro sea online
PROB_ONLINE = {
    "Viajes": 0.55, "Entretenimiento": 0.50, "Servicios": 0.50, "Educacion": 0.45,
    "Electronica": 0.35, "Ropa": 0.25, "Hogar": 0.20, "Salud": 0.10,
    "Supermercado": 0.10, "Farmacia": 0.08, "Restaurante": 0.05, "Combustible": 0.0,
}

# Patrones plantados
TERMINAL_COMPROMETIDO = "POS-CUS-COM-99"
VICTIMAS_CPP = 12            # de las victimas de tarjeta_clonada
CLIENTES_SANOS_EN_CPP = 20   # clientes sin fraude que tambien compraron ahi
DIAS_ANTES_FRAUDE = (3, 25)
PASARELA = "PASARELA-PAY-01"
USO_PASARELA = {"phishing_vishing": 10, "sim_swap": 10, "toma_de_cuenta": 7}


def _codigo(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.replace(" ", "")[:3].upper()


def separar_comercio(merchant_category: str):
    """'Viajes (Madrid, España)' -> ('Viajes', 'Madrid'); 'Viajes' -> ('Viajes', None)."""
    if "(" in merchant_category:
        rubro, resto = merchant_category.split("(", 1)
        return rubro.strip(), resto.split(",")[0].strip()
    return merchant_category.strip(), None


def terminal_pos(rng, ciudad, rubro):
    return f"POS-{_codigo(ciudad)}-{_codigo(rubro)}-{rng.randint(1, TERMINALES_POS_POR_CIUDAD_RUBRO):02d}"


def terminal_web(rng, rubro):
    return f"WEB-{_codigo(rubro)}-{rng.randint(1, COMERCIOS_WEB_POR_RUBRO):02d}"


def otra_ciudad(rng, ciudad_cliente, preferida=None, prob_preferida=0.0):
    if preferida and preferida != ciudad_cliente and rng.random() < prob_preferida:
        return preferida
    return rng.choice([c for c in CIUDADES_PERU if c != ciudad_cliente])


def contexto_legitimo(rng, rubro, ciudad_intl, ciudad_cliente):
    if rng.random() < PROB_ONLINE.get(rubro, 0.2):
        if rubro in ("Servicios", "Entretenimiento") and rng.random() < 0.15:
            return "APP", "APP", terminal_web(rng, rubro), "Online"
        return "INT", "NO_PRESENTE", terminal_web(rng, rubro), "Online"
    ciudad = ciudad_intl or (ciudad_cliente if rng.random() < 0.85 else otra_ciudad(rng, ciudad_cliente))
    modo = rng.choices(["CHIP", "CONTACTLESS", "BANDA", "FALLBACK"], weights=[62, 33, 4, 1])[0]
    return "POS", modo, terminal_pos(rng, ciudad, rubro), ciudad


def contexto_reclamo(rng, modalidad, rubro, ciudad_intl, ciudad_cliente):
    """Contexto coherente con lo que el cliente relata en la llamada."""
    if modalidad in ("tarjeta_clonada", "comercio_comprometido"):
        ciudad = ciudad_intl or otra_ciudad(rng, ciudad_cliente, preferida="Lima",
                                            prob_preferida=0.6 if modalidad == "tarjeta_clonada" else 0.0)
        modo = "BANDA" if rng.random() < 0.65 else "FALLBACK"
        return "POS", modo, terminal_pos(rng, ciudad, rubro), ciudad
    if modalidad == "robo_perdida_tarjeta":
        ciudad = ciudad_intl or ciudad_cliente
        modo = "CONTACTLESS" if rng.random() < 0.8 else "CHIP"
        return "POS", modo, terminal_pos(rng, ciudad, rubro), ciudad
    if modalidad in ("phishing_vishing", "toma_de_cuenta", "sim_swap"):
        prob_app = {"phishing_vishing": 0.4, "toma_de_cuenta": 0.7, "sim_swap": 0.75}[modalidad]
        if rng.random() < prob_app:
            return "APP", "APP", terminal_web(rng, rubro), "Online"
        return "INT", "NO_PRESENTE", terminal_web(rng, rubro), "Online"
    if modalidad == "suscripcion_no_autorizada":
        return "INT", "NO_PRESENTE", terminal_web(rng, rubro), "Online"
    # fraude_amigable, no_identificado: compra con apariencia normal
    return contexto_legitimo(rng, rubro, ciudad_intl, ciudad_cliente)


def main():
    rng = random.Random(SEMILLA)
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ct.card_txn_id, ct.card_id, ct.txn_date, ct.merchant_category,
                   cu.city AS ciudad_cliente, l.modalidad_real
            FROM card_transactions ct
            JOIN cards c ON c.card_id = ct.card_id
            JOIN customers cu ON cu.customer_id = c.customer_id
            LEFT JOIN llamadas l ON l.card_txn_id = ct.card_txn_id
            WHERE ct.canal IS NULL
            ORDER BY ct.card_txn_id;
        """)
        filas = cur.fetchall()

        # Pasarela: se elige de antemano que reclamos digitales se cobraron por ahi
        reclamos_por_modalidad = {}
        for f in filas:
            if f["modalidad_real"]:
                reclamos_por_modalidad.setdefault(f["modalidad_real"], []).append(f["card_txn_id"])
        por_pasarela = set()
        for modalidad, n in USO_PASARELA.items():
            candidatos = reclamos_por_modalidad.get(modalidad, [])
            por_pasarela.update(rng.sample(candidatos, min(n, len(candidatos))))

        actualizaciones = []
        for f in filas:
            rubro, ciudad_intl = separar_comercio(f["merchant_category"])
            if f["modalidad_real"]:
                canal, modo, terminal, ciudad = contexto_reclamo(
                    rng, f["modalidad_real"], rubro, ciudad_intl, f["ciudad_cliente"])
            else:
                canal, modo, terminal, ciudad = contexto_legitimo(rng, rubro, ciudad_intl, f["ciudad_cliente"])
            plantado = f["card_txn_id"] in por_pasarela
            if plantado:
                terminal = PASARELA
            actualizaciones.append((canal, modo, terminal, ciudad, plantado, f["card_txn_id"]))

        cur.executemany(
            """
            UPDATE card_transactions
            SET canal = %s, modo_entrada = %s, terminal_id = %s, ciudad_comercio = %s, es_plantado = %s
            WHERE card_txn_id = %s;
            """,
            actualizaciones,
        )
        print(f"Transacciones completadas: {len(actualizaciones)} "
              f"({len(por_pasarela)} reclamos digitales cobrados por {PASARELA}).")

        # Punto de compromiso: compras previas de las victimas de clonacion en el grifo de Cusco
        cur.execute("SELECT COUNT(*) AS n FROM card_transactions WHERE terminal_id = %s;", (TERMINAL_COMPROMETIDO,))
        if cur.fetchone()["n"]:
            print(f"El punto de compromiso {TERMINAL_COMPROMETIDO} ya existe; no se vuelve a plantar.")
        else:
            cur.execute("""
                SELECT ct.card_id, ct.txn_date
                FROM llamadas l JOIN card_transactions ct ON ct.card_txn_id = l.card_txn_id
                WHERE l.modalidad_real = 'tarjeta_clonada'
                ORDER BY l.call_id;
            """)
            victimas = rng.sample(cur.fetchall(), VICTIMAS_CPP)
            cur.execute("""
                SELECT card_id FROM cards
                WHERE card_id NOT IN (
                    SELECT ct.card_id FROM llamadas l JOIN card_transactions ct ON ct.card_txn_id = l.card_txn_id
                )
                ORDER BY card_id;
            """)
            sanas = rng.sample([r["card_id"] for r in cur.fetchall()], CLIENTES_SANOS_EN_CPP)
            fechas_fraude = [v["txn_date"] for v in victimas]

            nuevas = []
            for v in victimas:
                fecha = v["txn_date"] - timedelta(days=rng.randint(*DIAS_ANTES_FRAUDE), hours=rng.randint(0, 10))
                nuevas.append((v["card_id"], fecha))
            for card_id in sanas:
                base = rng.choice(fechas_fraude)
                nuevas.append((card_id, base - timedelta(days=rng.randint(-40, 40), hours=rng.randint(0, 10))))

            cur.executemany(
                """
                INSERT INTO card_transactions (card_id, txn_date, merchant_category, amount, is_fraud,
                                               canal, modo_entrada, terminal_id, ciudad_comercio, es_plantado)
                VALUES (%s, %s, 'Combustible', %s, FALSE, 'POS', %s, %s, 'Cusco', TRUE);
                """,
                [
                    (card_id, fecha, round(rng.uniform(40, 220), 2),
                     rng.choice(["CHIP", "CONTACTLESS"]), TERMINAL_COMPROMETIDO)
                    for card_id, fecha in nuevas
                ],
            )
            print(f"Punto de compromiso {TERMINAL_COMPROMETIDO}: {VICTIMAS_CPP} victimas de clonacion "
                  f"y {CLIENTES_SANOS_EN_CPP} clientes sin fraude compraron ahi.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
