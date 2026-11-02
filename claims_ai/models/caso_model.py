"""
Casos del Agente 1: llamadas pendientes de tipificar, tickets del cliente,
persistencia de tipificaciones y métricas. Incluye las reglas del dominio para
normalizar la modalidad predicha y la confianza.
"""
import json

from claims_ai.models.manual_model import MODALIDADES_VALIDAS, VERSION_MANUAL


def obtener_casos_pendientes(conn, limite=None, reprocesar_version: str = None, ids=None):
    """
    Obtiene las llamadas no tipificadas enriquecidas con información
    transaccional y del cliente, excluyendo cualquier etiqueta previa (ground truth).
    Con reprocesar_version, devuelve en cambio las ya tipificadas con otra versión de las
    instrucciones (así un reprocesamiento interrumpido continúa donde quedó).
    ids: limita a esas llamadas (y al reprocesar, las fuerza aunque ya tengan la versión actual).
    """
    with conn.cursor() as cur:
        query = """
            SELECT
                l.call_id,
                l.customer_id,
                l.canal AS call_canal,
                l.fecha_llamada,
                l.duracion_segundos,
                l.transcript,
                l.modalidad_real,
                ct.card_txn_id,
                ct.card_id,
                ct.txn_date,
                ct.merchant_category,
                ct.amount AS txn_amount,
                ct.canal AS txn_canal,
                ct.modo_entrada AS txn_modo_entrada,
                ct.ciudad_comercio AS txn_ciudad_comercio,
                c.card_type,
                c.credit_limit,
                cu.city AS customer_city,
                cu.occupation AS customer_occupation,
                cu.annual_income AS customer_income,
                cu.credit_score AS customer_credit_score
            FROM llamadas l
            LEFT JOIN tipificaciones t ON t.call_id = l.call_id
            LEFT JOIN card_transactions ct ON ct.card_txn_id = l.card_txn_id
            LEFT JOIN cards c ON c.card_id = ct.card_id
            LEFT JOIN customers cu ON cu.customer_id = l.customer_id
            WHERE (CASE WHEN %(reprocesar)s::text IS NULL THEN t.tipificacion_id IS NULL
                        ELSE t.tipificacion_id IS NOT NULL
                             -- con ids explícitos se reprocesan aunque ya tengan la versión actual
                             AND (t.prompt_version IS DISTINCT FROM %(reprocesar)s::text
                                  OR %(ids)s::int[] IS NOT NULL) END)
              AND (%(ids)s::int[] IS NULL OR l.call_id = ANY(%(ids)s::int[]))
            ORDER BY l.call_id
            LIMIT %(limite)s
        """
        cur.execute(query, {"reprocesar": reprocesar_version, "ids": list(ids) if ids else None,
                            "limite": limite})
        return cur.fetchall()


def obtener_historial_tarjeta(conn, card_id, card_txn_id, txn_date, dias: int = 90) -> list:
    """
    Compras de la tarjeta en los `dias` previos a la transacción reclamada (sin incluirla).
    No devuelve la marca de fraude: el Agente 1 no debe ver etiquetas.
    """
    if not card_id or not txn_date:
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT txn_date, merchant_category, amount, canal, modo_entrada, ciudad_comercio
            FROM card_transactions
            WHERE card_id = %s AND card_txn_id <> %s
              AND txn_date < %s AND txn_date >= %s::timestamp - make_interval(days => %s)
            ORDER BY txn_date;
        """, (card_id, card_txn_id, txn_date, txn_date, dias))
        return cur.fetchall()


def obtener_tickets_cliente(conn, customer_id, fecha_llamada, dias=180, limite=5):
    """Tickets de soporte del cliente abiertos en los `dias` previos a la llamada."""
    if not customer_id or not fecha_llamada:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT issue_type, date_opened, date_resolved, status
            FROM support_tickets
            WHERE customer_id = %s
              AND date_opened <= %s::date
              AND date_opened >= %s::date - %s
            ORDER BY date_opened DESC
            LIMIT %s
            """,
            (customer_id, fecha_llamada, fecha_llamada, dias, limite),
        )
        return cur.fetchall()


def normalizar_confianza(valor):
    """Convierte ai_confianza del LLM a float 0-100; None si no viene o no es numérico."""
    try:
        return max(0.0, min(100.0, float(valor)))
    except (TypeError, ValueError):
        return None


def etiqueta_confianza(score):
    if score is None:
        return "media"
    if score >= 80:
        return "alta"
    if score >= 50:
        return "media"
    return "baja"


# Situación del caso según el Agente 1 (fichas 1/4 = fraude, 2 = no fraude, 5 = ilegible)
SITUACIONES = {"fraude", "no_fraude", "ilegible"}


def iniciar_corrida_a1(conn, modelo: str, prompt_version: str, version_manual: str) -> int:
    """Registra el inicio de una corrida del Agente 1 (trazabilidad por corrida)."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO corridas_agente1 (modelo, prompt_version, version_manual)
            VALUES (%s, %s, %s) RETURNING corrida_a1_id;
        """, (modelo, prompt_version, version_manual))
        corrida_id = cur.fetchone()["corrida_a1_id"]
    conn.commit()
    return corrida_id


def cerrar_corrida_a1(conn, corrida_a1_id: int, n_casos: int, n_fallidos: int):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE corridas_agente1 SET fin = NOW(), n_casos = %s, n_fallidos = %s
            WHERE corrida_a1_id = %s;
        """, (n_casos, n_fallidos, corrida_a1_id))
    conn.commit()


def guardar_tipificacion(conn, call_id: int, modalidad_real: str, payload: dict, contexto_entrada: str = None,
                         trazabilidad: dict = None):
    """
    Persiste la tipificación v3.0 completa en PostgreSQL. trazabilidad: corrida_a1_id, modelo,
    prompt_version, version_manual y datos_enmascarados (qué se ocultó antes de la IA).
    """
    traza = trazabilidad or {}
    situacion = str(payload.get("ai_situacion") or "").strip().lower().replace(" ", "_")
    situacion = situacion if situacion in SITUACIONES else None
    evidencia = payload.get("ai_evidencia")
    evidencia = evidencia if isinstance(evidencia, list) else None
    tipificacion = payload.get("ai_tipificacion", "no_identificado").lower().strip()
    
    # Normalizar tipificación para evaluación contra modalidad_real
    modalidad_normalizada = tipificacion
    for mod in MODALIDADES_VALIDAS:
        if mod in tipificacion:
            modalidad_normalizada = mod
            break

    correcta = (modalidad_normalizada == modalidad_real) if modalidad_real else None
    confianza_score = normalizar_confianza(payload.get("ai_confianza"))
    confianza = etiqueta_confianza(confianza_score)
    justificacion = payload.get("ai_razonamiento_oculto") or payload.get("ai_insights")

    # Archivar, borrar e insertar van en una sola transacción: o se hace todo o nada
    try:
        # Reprocesamiento: la ficha anterior se archiva completa y se conserva la decisión del analista
        with conn.cursor() as cur:
            cur.execute("""
                SELECT estado_revision, tipologia_analista, analista_revision, fecha_revision
                FROM tipificaciones WHERE call_id = %s ORDER BY tipificacion_id DESC LIMIT 1;
            """, (call_id,))
            revision = cur.fetchone() or {}
            cur.execute("""
                INSERT INTO tipificaciones_historial (call_id, tipificacion_id, ficha, motivo)
                SELECT call_id, tipificacion_id, to_jsonb(t), %s FROM tipificaciones t WHERE call_id = %s;
            """, (f"reprocesada con instrucciones {traza.get('prompt_version') or '—'}", call_id))
            cur.execute("DELETE FROM tipificaciones WHERE call_id = %s;", (call_id,))
            cur.execute(
                """
                INSERT INTO tipificaciones (
                    call_id, modalidad_predicha, confianza, confianza_score, justificacion, correcta,
                    ai_razonamiento_oculto, ai_dialogo_limpio, ai_resumen_caso, ai_resumen_caso_tags,
                    ai_tipificacion, ai_tipificacion_tags, ai_estado_tipificacion,
                    ai_insights, ai_insights_tags, ai_accion_recomendada, ai_accion_recomendada_tags,
                    ai_blindaje, ai_blindaje_tags, raw_payload, ai_contexto_entrada,
                    corrida_a1_id, modelo, prompt_version, version_manual, datos_enmascarados,
                    ai_situacion, ai_motivo_situacion, ai_evidencia,
                    estado_revision, tipologia_analista, analista_revision, fecha_revision
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                );
                """,
                (
                    call_id,
                    modalidad_normalizada,
                    confianza,
                    confianza_score,
                    justificacion,
                    correcta,
                    payload.get("ai_razonamiento_oculto"),
                    payload.get("ai_dialogo_limpio"),
                    payload.get("ai_resumen_caso"),
                    payload.get("ai_resumen_caso_tags"),
                    payload.get("ai_tipificacion"),
                    payload.get("ai_tipificacion_tags"),
                    payload.get("ai_estado_tipificacion"),
                    payload.get("ai_insights"),
                    payload.get("ai_insights_tags"),
                    payload.get("ai_accion_recomendada"),
                    payload.get("ai_accion_recomendada_tags"),
                    payload.get("ai_blindaje"),
                    payload.get("ai_blindaje_tags"),
                    json.dumps(payload, ensure_ascii=False),
                    contexto_entrada,
                    traza.get("corrida_a1_id"),
                    traza.get("modelo"),
                    traza.get("prompt_version"),
                    traza.get("version_manual"),
                    json.dumps(traza.get("datos_enmascarados") or {}),
                    situacion,
                    payload.get("ai_motivo_situacion"),
                    json.dumps(evidencia, ensure_ascii=False) if evidencia is not None else None,
                    revision.get("estado_revision") or "Pendiente",
                    revision.get("tipologia_analista"),
                    revision.get("analista_revision"),
                    revision.get("fecha_revision"),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def obtener_metricas(conn) -> dict:
    """Métricas del Agente 1: acierto, confianza, distribución por modalidad y ejemplos de blindaje."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM tipificaciones;")
        total = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS correctas FROM tipificaciones WHERE correcta = TRUE;")
        correctas = cur.fetchone()["correctas"]

        cur.execute("SELECT AVG(confianza_score) AS conf, COUNT(confianza_score) AS n FROM tipificaciones;")
        conf_row = cur.fetchone()

        cur.execute("""
            SELECT COALESCE(ai_tipificacion, modalidad_predicha, 'desconocido') AS modalidad, COUNT(*) AS n
            FROM tipificaciones
            GROUP BY COALESCE(ai_tipificacion, modalidad_predicha, 'desconocido')
            ORDER BY n DESC;
        """)
        por_modalidad = cur.fetchall()

        cur.execute("""
            SELECT call_id, COALESCE(ai_tipificacion, modalidad_predicha, 'desconocido') AS ai_tipificacion, ai_blindaje
            FROM tipificaciones
            WHERE ai_blindaje IS NOT NULL AND ai_blindaje != ''
            ORDER BY tipificacion_id DESC
            LIMIT 4;
        """)
        ejemplos_blindaje = cur.fetchall()

    return {
        "total": total,
        "correctas": correctas,
        "confianza": conf_row,
        "por_modalidad": por_modalidad,
        "ejemplos_blindaje": ejemplos_blindaje,
    }


def listar_casos_analizados(conn) -> list:
    """Casos tipificados por el Agente 1, con los datos principales de la transacción reclamada."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.call_id,
                COALESCE(t.ai_tipificacion, t.modalidad_predicha) AS modalidad,
                t.ai_estado_tipificacion,
                l.modalidad_real,
                t.correcta,
                t.confianza,
                t.confianza_score,
                ct.amount,
                ct.merchant_category,
                ct.canal AS canal_txn,
                ct.modo_entrada,
                ct.ciudad_comercio,
                ct.terminal_id,
                c.credit_limit,
                cu.city AS ciudad_cliente,
                l.canal AS canal_reclamo,
                l.fecha_llamada,
                t.ai_resumen_caso, t.ai_insights, t.ai_accion_recomendada, t.ai_blindaje,
                COALESCE(t.estado_revision, 'Pendiente') AS estado_revision, t.tipologia_analista,
                t.ai_situacion, t.created_at
            FROM tipificaciones t
            JOIN llamadas l ON l.call_id = t.call_id
            LEFT JOIN card_transactions ct ON ct.card_txn_id = l.card_txn_id
            LEFT JOIN cards c ON c.card_id = ct.card_id
            LEFT JOIN customers cu ON cu.customer_id = l.customer_id
            ORDER BY t.call_id;
        """)
        casos = cur.fetchall()
    for caso in casos:
        caso.update(evaluar_ficha(caso))
        for campo in ("ai_resumen_caso", "ai_insights", "ai_accion_recomendada", "ai_blindaje"):
            caso.pop(campo, None)   # la lista no necesita el texto completo
    return casos


def obtener_detalle_caso(conn, call_id: int):
    """
    Todo lo que entró y salió del Agente 1 para una llamada: la llamada original, la
    transacción, el cliente, sus tickets, la ficha generada y los patrones vigentes
    que incluyen el caso. Devuelve None si la llamada no fue analizada.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                l.call_id, l.customer_id, l.canal AS canal_reclamo, l.fecha_llamada, l.duracion_segundos,
                l.transcript, l.modalidad_real,
                ct.card_txn_id, ct.txn_date, ct.merchant_category, ct.amount, ct.canal AS canal_txn,
                ct.modo_entrada, ct.ciudad_comercio, ct.terminal_id,
                c.card_type, c.credit_limit,
                cu.city AS ciudad_cliente, cu.occupation, cu.annual_income, cu.credit_score,
                t.ai_tipificacion, t.modalidad_predicha, t.ai_estado_tipificacion, t.correcta,
                t.confianza, t.confianza_score, t.ai_dialogo_limpio, t.ai_razonamiento_oculto,
                t.ai_resumen_caso, t.ai_insights, t.ai_accion_recomendada, t.ai_blindaje,
                t.ai_tipificacion_tags, t.ai_insights_tags, t.ai_resumen_caso_tags, t.ai_blindaje_tags,
                t.ai_contexto_entrada, t.created_at,
                COALESCE(t.estado_revision, 'Pendiente') AS estado_revision, t.tipologia_analista,
                t.analista_revision, t.fecha_revision, t.version_manual, t.corrida_a1_id, t.modelo AS modelo_ia,
                t.prompt_version, t.datos_enmascarados, t.ai_situacion, t.ai_motivo_situacion, t.ai_evidencia,
                ct.card_id, c.issue_date, c.expiry_date, c.status AS estado_tarjeta
            FROM tipificaciones t
            JOIN llamadas l ON l.call_id = t.call_id
            LEFT JOIN card_transactions ct ON ct.card_txn_id = l.card_txn_id
            LEFT JOIN cards c ON c.card_id = ct.card_id
            LEFT JOIN customers cu ON cu.customer_id = l.customer_id
            WHERE t.call_id = %s
            ORDER BY t.tipificacion_id DESC
            LIMIT 1;
        """, (call_id,))
        caso = cur.fetchone()
        if caso is None:
            return None

        cur.execute("""
            SELECT p.patron_id, p.titulo
            FROM patrones p
            WHERE COALESCE(p.vigente, TRUE) AND p.casos_asociados @> %s::jsonb
            ORDER BY p.patron_id;
        """, (json.dumps([call_id]),))
        patrones = cur.fetchall()

    tickets = obtener_tickets_cliente(conn, caso["customer_id"], caso["fecha_llamada"])

    # Qué datos recibió la IA según la versión con la que se analizó el caso:
    # el texto exacto se guarda desde v3.3; los tickets se agregaron junto con la
    # confianza numérica (v3.1); canal, modo de entrada, ciudad del comercio,
    # ingreso y score crediticio, en v3.2.
    contexto_exacto = caso["ai_contexto_entrada"] is not None
    entradas = {
        "contexto_exacto_guardado": contexto_exacto,
        "tickets": contexto_exacto or caso["confianza_score"] is not None,
        "contexto_transaccional_ampliado": contexto_exacto,
    }
    caso.update(evaluar_ficha({**caso, "modalidad": caso["ai_tipificacion"] or caso["modalidad_predicha"],
                               "amount": caso["amount"]}))
    caso["version_manual_vigente"] = VERSION_MANUAL
    revisiones = obtener_revisiones_caso(conn, call_id)
    return {
        "caso": caso, "tickets": tickets, "patrones": patrones, "entradas_ia": entradas,
        "revisiones": revisiones,
        "transacciones": obtener_transacciones_tarjeta(conn, caso),
        "cliente": obtener_cliente(conn, caso["customer_id"]),
        "linea_tiempo": construir_linea_tiempo(conn, caso, tickets, patrones, revisiones),
    }


# ---------------------------------------------------------------------------
# Estado de la ficha y nivel de riesgo (reglas deterministas, sin IA)
# ---------------------------------------------------------------------------
CAMPOS_FICHA = {
    "ai_resumen_caso": "resumen", "ai_insights": "hallazgos",
    "ai_accion_recomendada": "acción recomendada", "ai_blindaje": "regla de blindaje",
}
MODOS_CLONABLES = {"BANDA", "FALLBACK"}
MODALIDADES_CREDENCIALES = {"phishing_vishing", "sim_swap", "toma_de_cuenta"}


def evaluar_ficha(caso: dict) -> dict:
    """
    Estado de la ficha del Agente 1:
      - Sin tipología: la modalidad no está en el Manual (o es no_identificado).
      - Incompleto: falta algún campo de la ficha (resumen, hallazgos, acción o regla).
      - Completo: tiene tipología del Manual y todos los campos.
    Nivel de riesgo (Alto / Medio / Bajo), por puntos explicables sobre la transacción:
      monto (>= S/ 2.000: 2; >= S/ 800: 1), usa la mitad o más del límite (1), lectura
      clonable por banda o fallback (1), compra presencial en otra ciudad (1) y credenciales
      comprometidas por canal digital (1). Alto >= 3, Medio = 2.
    """
    modalidad = caso.get("modalidad")
    faltan = [nombre for campo, nombre in CAMPOS_FICHA.items() if not caso.get(campo)]
    if not modalidad or modalidad == "no_identificado" or modalidad not in MODALIDADES_VALIDAS:
        estado, motivo = "Sin tipología", "La modalidad no corresponde al Manual vigente."
    elif faltan:
        estado, motivo = "Incompleto", "Falta: " + ", ".join(faltan) + "."
    else:
        estado, motivo = "Completo", "Tiene tipología del Manual y todos los campos de la ficha."

    monto = float(caso.get("amount") or 0)
    limite = float(caso.get("credit_limit") or 0)
    razones = []
    puntos = 0
    if monto >= 2000:
        puntos += 2; razones.append("monto de S/ 2.000 o más")
    elif monto >= 800:
        puntos += 1; razones.append("monto de S/ 800 o más")
    if limite and monto >= 0.5 * limite:
        puntos += 1; razones.append("usa la mitad o más del límite de la tarjeta")
    if caso.get("modo_entrada") in MODOS_CLONABLES:
        puntos += 1; razones.append("lectura por banda o fallback (clonable)")
    ciudad = caso.get("ciudad_comercio")
    if ciudad and ciudad != "Online" and ciudad != caso.get("ciudad_cliente"):
        puntos += 1; razones.append("compra presencial en otra ciudad")
    if caso.get("canal_txn") in ("APP", "INT") and modalidad in MODALIDADES_CREDENCIALES:
        puntos += 1; razones.append("credenciales comprometidas por canal digital")
    riesgo = "Alto" if puntos >= 3 else ("Medio" if puntos == 2 else "Bajo")

    variante = variante_ficha(caso, estado)
    return {"estado_ficha": estado, "estado_motivo": motivo,
            "nivel_riesgo": riesgo, "riesgo_razones": razones or ["sin señales adicionales"], **variante}


# Variantes de ficha de la propuesta técnica (lámina 16). Las variantes 2 (no fraude) y
# 5 (transcripción ilegible) requieren que el Agente 1 las detecte en su análisis.
VARIANTES = {
    1: ("Fraude con tipología", "Confirma o corrige la tipología. Alimenta al Agente 2."),
    2: ("No fraude", "Cierra el caso o lo reabre con un comentario."),
    3: ("Caso incompleto", "Completa el insumo o lo deja en espera; nada se descarta."),
    4: ("Sin tipología", "Lo marca como candidato a nueva modalidad para el Agente 2."),
    5: ("Transcripción ilegible", "Solicita la grabación o lo excluye de la corrida."),
}


def variante_ficha(caso: dict, estado: str) -> dict:
    if caso.get("ai_situacion") == "ilegible":
        numero = 5
    elif caso.get("ai_situacion") == "no_fraude":
        numero = 2
    elif (caso.get("estado_revision") == "Nueva modalidad" or caso.get("ai_estado_tipificacion") == "Nueva"
            or estado == "Sin tipología"):
        numero = 4
    elif estado == "Incompleto" or caso.get("amount") is None:
        numero = 3
    else:
        numero = 1
    nombre, accion = VARIANTES[numero]
    return {"variante": numero, "variante_nombre": nombre, "variante_accion": accion}


# ---------------------------------------------------------------------------
# Métricas de calidad del Agente 1 (tipificación frente a la modalidad real)
# ---------------------------------------------------------------------------
def obtener_calidad(conn) -> dict:
    """
    Exactitud, precisión, recall, F1, falsos positivos/negativos por modalidad y matriz de
    confusión. Como todas las llamadas son reclamos de fraude, un "falso positivo" de una
    modalidad es un caso que la IA asignó a esa modalidad siendo de otra.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.call_id, t.modalidad_predicha AS predicha, l.modalidad_real AS real,
                   t.confianza_score, t.confianza
            FROM tipificaciones t
            JOIN llamadas l ON l.call_id = t.call_id
            WHERE l.modalidad_real IS NOT NULL;
        """)
        filas = cur.fetchall()

    total = len(filas)
    if not total:
        return {"total": 0}
    etiquetas = sorted({f["real"] for f in filas} | {f["predicha"] for f in filas})
    matriz = {r: {p: [] for p in etiquetas} for r in etiquetas}
    for f in filas:
        matriz[f["real"]][f["predicha"]].append(f["call_id"])

    por_modalidad = []
    for m in etiquetas:
        vp = len(matriz[m][m])
        fp = sum(len(matriz[r][m]) for r in etiquetas if r != m)
        fn = sum(len(matriz[m][p]) for p in etiquetas if p != m)
        soporte = vp + fn
        precision = vp / (vp + fp) if vp + fp else None
        recall = vp / soporte if soporte else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if soporte else None)
        por_modalidad.append({
            "modalidad": m, "casos_reales": soporte, "predichos": vp + fp,
            "verdaderos_positivos": vp, "falsos_positivos": fp, "falsos_negativos": fn,
            "precision": precision, "recall": recall, "f1": f1,
        })

    correctas = sum(len(matriz[m][m]) for m in etiquetas)
    validas = [x for x in por_modalidad if x["casos_reales"]]
    macro = lambda k: sum(x[k] or 0 for x in validas) / len(validas) if validas else None

    # ¿La confianza de la IA anticipa sus aciertos? (solo casos con confianza numérica)
    conf = {True: [], False: []}
    for f in filas:
        if f["confianza_score"] is not None:
            conf[f["predicha"] == f["real"]].append(float(f["confianza_score"]))
    promedio = lambda xs: round(sum(xs) / len(xs), 1) if xs else None

    errores = sorted(
        ({"real": r, "predicha": p, "casos": len(matriz[r][p]), "call_ids": matriz[r][p]}
         for r in etiquetas for p in etiquetas if r != p and matriz[r][p]),
        key=lambda e: -e["casos"])

    return {
        "total": total,
        "correctas": correctas,
        "exactitud": correctas / total,
        "precision_macro": macro("precision"),
        "recall_macro": macro("recall"),
        "f1_macro": macro("f1"),
        "por_modalidad": por_modalidad,
        "etiquetas": etiquetas,
        "matriz": [[len(matriz[r][p]) for p in etiquetas] for r in etiquetas],
        "matriz_casos": [[matriz[r][p] for p in etiquetas] for r in etiquetas],
        "confianza_aciertos": promedio(conf[True]),
        "confianza_errores": promedio(conf[False]),
        "casos_con_confianza": len(conf[True]) + len(conf[False]),
        "errores_frecuentes": errores,
    }


# ---------------------------------------------------------------------------
# Detalle de caso: transacciones, cliente, historial y línea de tiempo
# ---------------------------------------------------------------------------
def obtener_transacciones_tarjeta(conn, caso: dict, limite: int = 30) -> list:
    """Compras de la tarjeta alrededor del fraude (las más recientes hasta 30 días después)."""
    if not caso.get("card_id"):
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT card_txn_id, txn_date, merchant_category, amount, is_fraud, canal, modo_entrada,
                   terminal_id, ciudad_comercio, card_txn_id = %s AS es_reclamada
            FROM card_transactions
            WHERE card_id = %s AND txn_date <= %s::timestamp + interval '30 days'
            ORDER BY txn_date DESC
            LIMIT %s;
        """, (caso["card_txn_id"], caso["card_id"], caso["txn_date"], limite))
        return cur.fetchall()


def obtener_cliente(conn, customer_id) -> dict:
    """Perfil del cliente con sus cuentas y tarjetas."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT customer_id, city, occupation, annual_income, credit_score, join_date
            FROM customers WHERE customer_id = %s;
        """, (customer_id,))
        perfil = cur.fetchone()
        cur.execute("""
            SELECT account_id, account_type, balance, status, open_date
            FROM accounts WHERE customer_id = %s ORDER BY account_id;
        """, (customer_id,))
        cuentas = cur.fetchall()
        cur.execute("""
            SELECT card_id, card_type, credit_limit, status, issue_date, expiry_date
            FROM cards WHERE customer_id = %s ORDER BY card_id;
        """, (customer_id,))
        tarjetas = cur.fetchall()
    return {"perfil": perfil, "cuentas": cuentas, "tarjetas": tarjetas}


def obtener_revisiones_caso(conn, call_id: int) -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT accion, tipologia_anterior, tipologia_nueva, comentario, analista, created_at
            FROM revisiones_caso WHERE call_id = %s ORDER BY created_at DESC;
        """, (call_id,))
        return cur.fetchall()


def construir_linea_tiempo(conn, caso: dict, tickets: list, patrones: list, revisiones: list) -> list:
    """
    Eventos reales del caso en orden: compras previas en un punto de compromiso detectado,
    la transacción reclamada, tickets, la llamada, el análisis de la IA, los patrones que
    lo incluyen y las decisiones del analista.
    """
    eventos = []
    with conn.cursor() as cur:
        if caso.get("card_id") and caso.get("txn_date"):
            cur.execute("""
                SELECT ct.txn_date, ct.terminal_id, ct.merchant_category, ct.ciudad_comercio
                FROM card_transactions ct
                WHERE ct.card_id = %s AND NOT ct.is_fraud
                  AND ct.txn_date < %s AND ct.txn_date >= %s::timestamp - interval '30 days'
                  AND ct.terminal_id IN (
                      SELECT h.evidencia -> 'entidad_de_riesgo' ->> 'entidad_id'
                      FROM hipotesis h
                      WHERE h.vigente AND h.evidencia -> 'entidad_de_riesgo' ->> 'tipo' = 'punto_de_compromiso')
                ORDER BY ct.txn_date;
            """, (caso["card_id"], caso["txn_date"], caso["txn_date"]))
            for r in cur.fetchall():
                eventos.append({"fecha": r["txn_date"], "tipo": "compromiso", "titulo": "Compra en comercio sospechoso",
                                "detalle": f"{r['terminal_id']} · {r['merchant_category']} · {r['ciudad_comercio']}"})
        cur.execute("""
            SELECT MIN(created_at) AS fecha FROM patrones
            WHERE COALESCE(vigente, TRUE) AND casos_asociados @> %s::jsonb;
        """, (json.dumps([caso["call_id"]]),))
        fecha_patron = cur.fetchone()["fecha"]

    if caso.get("txn_date"):
        eventos.append({"fecha": caso["txn_date"], "tipo": "transaccion", "titulo": "Transacción reclamada",
                        "detalle": f"S/ {float(caso['amount']):,.2f} · {caso['merchant_category']} · "
                                   f"{caso.get('canal_txn') or '—'} / {caso.get('modo_entrada') or '—'}"})
    for t in tickets:
        eventos.append({"fecha": t["date_opened"], "tipo": "ticket", "titulo": "Ticket de soporte",
                        "detalle": f"{t['issue_type']} · {t['status']}"})
    if caso.get("fecha_llamada"):
        minutos = round((caso.get("duracion_segundos") or 0) / 60)
        eventos.append({"fecha": caso["fecha_llamada"], "tipo": "llamada", "titulo": "Llamada de reclamo",
                        "detalle": f"{caso.get('canal_reclamo') or '—'} · {minutos} min"})
    if caso.get("created_at"):
        eventos.append({"fecha": caso["created_at"], "tipo": "ia", "titulo": "Análisis del Agente 1",
                        "detalle": f"{caso.get('ai_tipificacion') or caso.get('modalidad_predicha')}"})
    if fecha_patron and patrones:
        eventos.append({"fecha": fecha_patron, "tipo": "patron", "titulo": "Incluido en patrón",
                        "detalle": " · ".join(p["titulo"][:60] for p in patrones)})
    for r in revisiones:
        eventos.append({"fecha": r["created_at"], "tipo": "analista", "titulo": f"Analista: {r['accion']}",
                        "detalle": r["comentario"] or (r["tipologia_nueva"] or "")})

    from datetime import date, datetime
    clave = lambda e: e["fecha"] if isinstance(e["fecha"], datetime) else datetime.combine(e["fecha"], datetime.min.time())
    return sorted(eventos, key=clave)


# Decisión del analista sobre la ficha del Agente 1
ACCIONES_CASO = {"Confirmar", "Corregir", "Nueva modalidad", "Comentario"}
ESTADO_POR_ACCION = {"Confirmar": "Confirmada", "Corregir": "Corregida", "Nueva modalidad": "Nueva modalidad"}


def guardar_revision_caso(conn, call_id: int, accion: str, comentario: str, analista: str,
                          tipologia: str = None) -> dict:
    """
    Registra la decisión del analista en la bitácora y, salvo un comentario, actualiza la ficha.
    'Corregir' exige una tipología del Manual; 'Nueva modalidad' marca el caso como candidato.
    La tipología fijada por el analista es la que usa el Agente 2 en su siguiente corrida.
    """
    if accion not in ACCIONES_CASO:
        raise ValueError(f"Acción no válida: {accion}")
    if accion == "Corregir" and tipologia not in MODALIDADES_VALIDAS:
        raise ValueError("Para corregir hay que elegir una tipología del Manual vigente.")
    if accion == "Comentario" and not (comentario or "").strip():
        raise ValueError("El comentario está vacío.")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(tipologia_analista, ai_tipificacion, modalidad_predicha) AS actual
                FROM tipificaciones WHERE call_id = %s ORDER BY tipificacion_id DESC LIMIT 1;
            """, (call_id,))
            fila = cur.fetchone()
            if fila is None:
                raise ValueError(f"La llamada {call_id} no fue analizada.")
            actual = fila["actual"]
            nueva = {"Confirmar": actual, "Corregir": tipologia}.get(accion)
            if accion in ESTADO_POR_ACCION:
                cur.execute("""
                    UPDATE tipificaciones
                    SET estado_revision = %s, tipologia_analista = COALESCE(%s, tipologia_analista),
                        analista_revision = %s, fecha_revision = NOW()
                    WHERE call_id = %s;
                """, (ESTADO_POR_ACCION[accion], nueva, analista, call_id))
            cur.execute("""
                INSERT INTO revisiones_caso (call_id, accion, tipologia_anterior, tipologia_nueva, comentario, analista)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (call_id, accion, actual, nueva, comentario, analista))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"accion": accion, "tipologia_anterior": actual, "tipologia_nueva": nueva}
