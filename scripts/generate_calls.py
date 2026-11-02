"""
Generador de llamadas/reclamos simulados para el Agente 1 de CLAIMS.AI
("Entender el caso": interpreta y tipifica cada llamada segun modalidades conocidas).

Usa los CSVs ya generados en ./data/ (customers, cards, card_transactions) para que
las llamadas sean coherentes: si el cliente menciona un monto o comercio, ese dato
existe realmente en la BD.

Uso:
    python scripts/generate_calls.py
Genera:
    ./data/llamadas.csv   -> una fila por llamada (metadata + transcript)
"""
import csv
import os
import random
from datetime import datetime, timedelta

random.seed(7)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RAIZ, "data")
OUT_FILE = os.path.join(DATA_DIR, "llamadas.csv")

MODALIDADES = [
    "tarjeta_clonada",
    "phishing_vishing",
    "robo_perdida_tarjeta",
    "toma_de_cuenta",
    "suscripcion_no_autorizada",
    "fraude_amigable",
    "sim_swap",
    "comercio_comprometido",
    "no_identificado",
]

# --- Plantillas de transcript por modalidad (con placeholders) --------------
PLANTILLAS = {
    "tarjeta_clonada": [
        "Buenas tardes, le hablo porque revise mi estado de cuenta y encontre un "
        "cargo de S/ {monto} en {comercio} que yo no hice. Mi tarjeta la tengo "
        "conmigo todo el tiempo, nunca la he prestado ni la he perdido. Ese cargo "
        "fue el {fecha} y yo ese dia ni siquiera estuve cerca de ahi.",
        "Hola, tengo un problema, me esta apareciendo un consumo raro de {monto} "
        "soles en {comercio}, fecha {fecha}. Mi tarjeta nunca ha salido de mi "
        "billetera, no entiendo como pudieron usarla.",
    ],
    "phishing_vishing": [
        "Buenas, me llamo alguien diciendo que era del banco, me pidio confirmar "
        "el codigo que me llego por mensaje de texto para 'verificar mi identidad' "
        "y despues de eso me salio un cargo de S/ {monto} en {comercio} que no "
        "reconozco, el {fecha}.",
        "Recibi un correo que parecia del banco pidiendome actualizar mis datos, "
        "puse mi clave ahi y ahora tengo un cargo de {monto} soles en {comercio} "
        "que no hice, fue el {fecha}.",
    ],
    "robo_perdida_tarjeta": [
        "Hola, quiero reportar que perdi mi tarjeta hace unos dias y ahora veo un "
        "cargo de S/ {monto} en {comercio} el {fecha} que yo no hice, debe haber "
        "sido alguien que la encontro.",
        "Me robaron la billetera la semana pasada y recien me doy cuenta que "
        "usaron mi tarjeta para un consumo de {monto} soles en {comercio}, fecha "
        "{fecha}. Ya habia reportado el robo pero no la habia bloqueado.",
    ],
    "toma_de_cuenta": [
        "Buenas, no puedo entrar a mi app del banco, dice que mi clave es "
        "incorrecta pero yo no la he cambiado. Y veo un cargo de S/ {monto} en "
        "{comercio} el {fecha} que definitivamente no hice yo.",
        "Alguien entro a mi cuenta y cambio mis datos de contacto, despues aparecio "
        "un cargo de {monto} soles en {comercio} el {fecha}. Yo no autorice nada "
        "de eso.",
    ],
    "suscripcion_no_autorizada": [
        "Hola, tengo un cargo recurrente de S/ {monto} de {comercio} que se repite "
        "cada mes desde hace tiempo y yo nunca me suscribi a nada de eso. La "
        "ultima vez que me cobraron fue el {fecha}.",
        "Me estan cobrando {monto} soles mensuales de {comercio}, no recuerdo "
        "haber aceptado ninguna suscripcion, quiero que me lo den de baja y me "
        "devuelvan lo cobrado el {fecha}.",
    ],
    "fraude_amigable": [
        "Disculpe, veo un cargo de S/ {monto} en {comercio} el {fecha} que no "
        "reconocia, pero espere, creo que fue mi hijo que uso mi tarjeta sin "
        "avisarme, dejeme confirmar con el.",
        "Reporto un cargo raro de {monto} soles en {comercio}, {fecha}... aunque "
        "ahora que lo pienso puede que haya sido mi pareja, tenemos tarjetas "
        "adicionales y a veces no nos avisamos.",
    ],
    "sim_swap": [
        "Mi celular dejo de tener señal de la nada hace dos dias, y cuando "
        "recupere linea vi que habian hecho un cargo de S/ {monto} en {comercio} "
        "el {fecha}. Creo que alguien duplico mi chip.",
        "Me quede sin señal en mi telefono por horas, y justo en ese tiempo "
        "aparecio un consumo de {monto} soles en {comercio}, fecha {fecha}, que "
        "yo no hice.",
    ],
    "comercio_comprometido": [
        "Hace poco compre en {comercio} con mi tarjeta fisica, y unos dias "
        "despues me aparecio otro cargo de S/ {monto} en el mismo tipo de "
        "comercio pero en otra ciudad, fecha {fecha}, que yo no hice.",
        "Fui a {comercio} la semana pasada, y ahora tengo un cargo de {monto} "
        "soles el {fecha} que no reconozco, para mi que clonaron mis datos ahi.",
    ],
    "no_identificado": [
        "Tengo un cargo de S/ {monto} en {comercio} el {fecha} que no reconozco, "
        "no tengo mas informacion, no perdi mi tarjeta ni comparti mis claves, "
        "simplemente no se de donde salio.",
        "No entiendo que paso, aparece un consumo de {monto} soles en {comercio}, "
        "{fecha}, y no tengo ninguna explicacion, nunca me ha pasado algo asi.",
    ],
}

# Todas las llamadas entran por el contact center (Genesys). GIRU no es un canal: es el sistema
# donde el asesor registra el reclamo, y queda fuera del MVP.
CANALES = ["contact_center"]


def cargar_csv(nombre):
    path = os.path.join(DATA_DIR, nombre)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def formatear_fecha(txn_date_str):
    dt = datetime.fromisoformat(txn_date_str)
    return dt.strftime("%d/%m/%Y")


def main():
    customers = cargar_csv("customers.csv")
    cards = cargar_csv("cards.csv")
    card_txns = cargar_csv("card_transactions.csv")

    customers_by_id = {c["customer_id"]: c for c in customers}
    cards_by_id = {c["card_id"]: c for c in cards}

    # Transacciones ya marcadas como fraude en la BD -> base principal de llamadas
    fraud_txns = [t for t in card_txns if t["is_fraud"] == "True"]

    # Algunas transacciones normales tambien generan llamadas (fraude amigable /
    # no identificado / comercio raro pero legitimo)
    normal_txns_sample = random.sample(
        [t for t in card_txns if t["is_fraud"] == "False"],
        k=min(40, len(card_txns)),
    )

    llamadas = []
    call_id = 1

    def crear_llamada(txn, modalidad):
        nonlocal call_id
        card = cards_by_id.get(txn["card_id"])
        if not card:
            return
        customer = customers_by_id.get(card["customer_id"])
        if not customer:
            return

        plantilla = random.choice(PLANTILLAS[modalidad])
        comercio = txn["merchant_category"].split(" (")[0]  # sin el sufijo de ciudad
        transcript = plantilla.format(
            monto=f"{float(txn['amount']):.2f}",
            comercio=comercio,
            fecha=formatear_fecha(txn["txn_date"]),
        )

        fecha_llamada = datetime.fromisoformat(txn["txn_date"]) + timedelta(
            days=random.randint(0, 5)
        )

        llamadas.append({
            "call_id": call_id,
            "customer_id": customer["customer_id"],
            "card_txn_id": txn["card_txn_id"],
            "canal": random.choice(CANALES),
            "fecha_llamada": fecha_llamada.isoformat(),
            "duracion_segundos": random.randint(90, 600),
            "transcript": transcript,
            "modalidad_real": modalidad,  # etiqueta real, para evaluar al agente despues
        })
        call_id += 1

    # Llamadas ligadas a fraude confirmado -> modalidades "duras"
    modalidades_fraude_confirmado = [
        "tarjeta_clonada", "phishing_vishing", "robo_perdida_tarjeta",
        "toma_de_cuenta", "sim_swap", "comercio_comprometido",
    ]
    for txn in fraud_txns:
        modalidad = random.choice(modalidades_fraude_confirmado)
        crear_llamada(txn, modalidad)

    # Llamadas ligadas a transacciones normales -> fraude amigable / suscripcion / no identificado
    modalidades_no_fraude = [
        "fraude_amigable", "suscripcion_no_autorizada", "no_identificado",
    ]
    for txn in normal_txns_sample:
        modalidad = random.choice(modalidades_no_fraude)
        crear_llamada(txn, modalidad)

    # Guardar CSV
    fieldnames = list(llamadas[0].keys())
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(llamadas)

    print(f"Generadas {len(llamadas)} llamadas simuladas en {OUT_FILE}")
    print("\nDistribucion por modalidad:")
    conteo = {}
    for l in llamadas:
        conteo[l["modalidad_real"]] = conteo.get(l["modalidad_real"], 0) + 1
    for mod, n in sorted(conteo.items(), key=lambda x: -x[1]):
        print(f"  {mod}: {n}")


if __name__ == "__main__":
    main()
