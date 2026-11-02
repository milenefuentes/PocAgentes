"""
Datos del portal web: resumen ejecutivo, patrones e hipótesis vigentes, relaciones
entre entidades y registro de la validación humana (con bitácora de revisiones).
"""
import os
from datetime import datetime

from claims_ai.models import caso_model, database, parametros_model
from claims_ai.models.manual_model import MANUAL_DATA, VERSION_MANUAL


# Validación humana: se guarda en la tabla `hipotesis` (estado vigente) y en
# `revisiones_hipotesis` (bitácora completa de cada decisión, para trazabilidad).
ESTADOS_VALIDOS = {"Pendiente revisión", "Validado", "En Investigación", "Descartado"}


ANALISTA_DEFAULT = "Analista de Fraude Senior"


def guardar_validacion(hipotesis_id, estado, comentario, analista):
    if estado not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado no válido: {estado}")
    conn = database.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hipotesis
                SET estado = %s, comentario_analista = %s, analista = %s, fecha_revision = NOW(),
                    decision_heredada = FALSE
                WHERE hipotesis_id = %s
                RETURNING hipotesis_id, memoria_id;
                """,
                (estado, comentario, analista, hipotesis_id),
            )
            fila = cur.fetchone()
            if fila is None:
                raise ValueError(f"No existe la hipótesis {hipotesis_id}")
            if fila["memoria_id"] is not None:
                cur.execute(
                    """
                    UPDATE memoria_patrones
                    SET estado = %s, comentario = %s, analista = %s, fecha_decision = NOW(), updated_at = NOW()
                    WHERE memoria_id = %s;
                    """,
                    (estado, comentario, analista, fila["memoria_id"]),
                )
            cur.execute(
                """
                INSERT INTO revisiones_hipotesis (hipotesis_id, estado, comentario, analista)
                VALUES (%s, %s, %s, %s);
                """,
                (hipotesis_id, estado, comentario, analista),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def obtener_datos_dashboard() -> dict:
    """Resumen ejecutivo, patrones, hipótesis, relaciones y casos recientes para el portal."""
    conn = database.connect()

    with conn.cursor() as cur:
        # 1. Contadores y métricas ejecutivas
        cur.execute("SELECT COUNT(*) AS total FROM patrones WHERE COALESCE(vigente, TRUE);")
        total_patrones = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS total, AVG(confianza) AS conf FROM hipotesis WHERE vigente;")
        row = cur.fetchone()
        total_hipotesis = row["total"]
        confianza_promedio = round(float(row["conf"])) if row["conf"] is not None else None

        # Precisión de A1: tipificaciones correctas sobre las evaluables (con modalidad real)
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(correcta) AS evaluables,
                   COUNT(*) FILTER (WHERE correcta) AS correctas
            FROM tipificaciones;
        """)
        row = cur.fetchone()
        casos_analizados = row["total"]
        precision_a1 = round(100.0 * row["correctas"] / row["evaluables"], 1) if row["evaluables"] else None

        # Solo lo nuevo le llega al analista (propuesta, lámina 18): los conocidos y los de bajo umbral
        # quedan registrados sin pedir decisión (el analista igual puede revisarlos si quiere)
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE estado = 'Pendiente revisión' AND clasificacion = 'Nuevo') AS pendientes,
                   COUNT(*) FILTER (WHERE estado = 'Pendiente revisión'
                                    AND COALESCE(clasificacion, '') <> 'Nuevo') AS registrados
            FROM hipotesis WHERE vigente;
        """)
        fila_h = cur.fetchone()
        hipotesis_pendientes, hipotesis_registradas = fila_h["pendientes"], fila_h["registrados"]

        # Estado de la ejecución: última corrida de cada agente y llamadas que faltan analizar
        cur.execute("""
            SELECT corrida_a1_id, inicio, fin, n_casos, n_fallidos
            FROM corridas_agente1 ORDER BY corrida_a1_id DESC LIMIT 1;
        """)
        corrida_a1 = cur.fetchone()
        cur.execute("""
            SELECT MAX(created_at) AS ultimo,
                   COUNT(*) FILTER (WHERE created_at::date = (SELECT MAX(created_at)::date FROM tipificaciones)) AS ese_dia
            FROM tipificaciones;
        """)
        ultimo_a1 = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*) AS n FROM llamadas l
            WHERE NOT EXISTS (SELECT 1 FROM tipificaciones t WHERE t.call_id = l.call_id);
        """)
        llamadas_pendientes = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS total FROM llamadas;")
        total_llamadas = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS total FROM card_transactions;")
        total_transacciones = cur.fetchone()["total"]

        # Tickets de soporte de los clientes cuyas llamadas ya tipificó A1
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM support_tickets st
            WHERE st.customer_id IN (
                SELECT l.customer_id FROM tipificaciones t JOIN llamadas l ON l.call_id = t.call_id
            );
        """)
        reclamos_analizados = cur.fetchone()["total"]

        cur.execute("""
            SELECT corrida_id, n_casos, k_clusters, created_at, omitidos_memoria
            FROM corridas_patrones ORDER BY corrida_id DESC LIMIT 1;
        """)
        corrida = cur.fetchone() or {}

        # Embudo del proceso: terminales de riesgo y estado de la revisión humana
        cur.execute("""
            SELECT COUNT(DISTINCT origen) AS total
            FROM relaciones_entidades
            WHERE corrida_id = %s AND origen_tipo = 'terminal';
        """, (corrida.get("corrida_id"),))
        entidades_riesgo = cur.fetchone()["total"]
        entidades_detalle = _detalle_entidades_riesgo(cur, corrida.get("corrida_id"))
        calidad_a1 = caso_model.obtener_calidad(conn)

        cur.execute("""
            SELECT estado, COUNT(*) AS n FROM hipotesis WHERE vigente GROUP BY estado;
        """)
        revision_por_estado = {r["estado"]: r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COALESCE(clasificacion, 'Sin clasificar') AS clasificacion, COUNT(*) AS n
            FROM hipotesis WHERE vigente GROUP BY 1;
        """)
        por_clasificacion = {r["clasificacion"]: r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE estado <> 'Pendiente revisión') AS evaluados
            FROM memoria_patrones;
        """)
        memoria_resumen = cur.fetchone()
        corrida_id = corrida.get("corrida_id")

        # 2. Patrones vigentes con la confianza y estado de su hipótesis
        cur.execute("""
            SELECT 
                p.patron_id, p.titulo, p.modalidad_asociada, p.descripcion, p.evidencia,
                p.num_casos, p.regla_propuesta, p.clusters_origen, p.impacto_total,
                p.distribuciones, p.analisis_reglas, p.accion_recomendada, p.ai_razonamiento_oculto,
                p.casos_asociados,
                h.hipotesis_id, h.confianza, h.confianza_detalle, h.nivel_impacto, h.evidencia AS evidencia_json,
                h.clasificacion, h.casos_nuevos, h.decision_heredada, h.prioridad, h.prioridad_detalle,
                m.veces_detectado, m.primera_corrida, m.ultima_corrida AS memoria_ultima_corrida,
                m.casos_vistos AS memoria_casos_vistos,
                COALESCE(h.estado, 'Pendiente revisión') AS estado_validacion
            FROM patrones p
            LEFT JOIN hipotesis h ON h.patron_id = p.patron_id
            LEFT JOIN memoria_patrones m ON m.memoria_id = h.memoria_id
            WHERE COALESCE(p.vigente, TRUE)
            ORDER BY p.patron_id;
        """)
        patrones = cur.fetchall()

        # 3. Hipótesis vigentes para validación humana
        cur.execute("""
            SELECT 
                h.hipotesis_id, h.patron_id, h.enunciado, h.modalidad, h.confianza,
                h.confianza_detalle, h.impacto_monto, h.nivel_impacto, h.num_casos,
                h.evidencia, h.estado, h.comentario_analista, h.analista, h.fecha_revision,
                h.clasificacion, h.casos_nuevos, h.decision_heredada, h.prioridad, h.prioridad_detalle,
                m.veces_detectado, m.primera_corrida, m.ultima_corrida AS memoria_ultima_corrida,
                m.casos_vistos AS memoria_casos_vistos,
                p.titulo
            FROM hipotesis h
            JOIN patrones p ON p.patron_id = h.patron_id
            LEFT JOIN memoria_patrones m ON m.memoria_id = h.memoria_id
            WHERE h.vigente
            ORDER BY h.hipotesis_id;
        """)
        hipotesis = cur.fetchall()

        # 4. Relaciones entre entidades de la última corrida de A2
        cur.execute("""
            SELECT origen_tipo, origen, destino_tipo, destino, peso
            FROM relaciones_entidades
            WHERE corrida_id = %s
            ORDER BY (origen_tipo = 'terminal') DESC, peso DESC
            LIMIT 70;
        """, (corrida_id,))
        relaciones = cur.fetchall()

        cur.execute("""
            SELECT COUNT(DISTINCT origen_tipo || ':' || origen) AS total
            FROM relaciones_entidades WHERE corrida_id = %s;
        """, (corrida_id,))
        total_entidades = cur.fetchone()["total"]

        # 5. Muestra de casos tipificados recientes (A1)
        cur.execute("""
            SELECT 
                call_id,
                COALESCE(ai_tipificacion, modalidad_predicha) AS modalidad,
                confianza,
                confianza_score,
                ai_resumen_caso,
                ai_blindaje,
                created_at
            FROM tipificaciones
            ORDER BY tipificacion_id DESC
            LIMIT 8;
        """)
        casos_recientes = cur.fetchall()

    conn.close()

    # Construcción del grafo de nodos y enlaces
    radio_por_tipo = {"modalidad": 22, "terminal": 20, "ciudad": 18, "comercio": 16, "tipo_tarjeta": 15,
                      "canal_txn": 15, "modo_entrada": 14, "canal_reclamo": 14}
    nodos = {}
    enlaces = []

    def get_or_create_node(tipo, nombre):
        node_id = f"{tipo}:{nombre}"
        if node_id not in nodos:
            nodos[node_id] = {"id": node_id, "label": nombre, "group": tipo,
                              "radius": radio_por_tipo.get(tipo, 15)}
        return node_id

    for rel in relaciones:
        origen = get_or_create_node(rel["origen_tipo"], rel["origen"])
        destino = get_or_create_node(rel["destino_tipo"], rel["destino"])
        enlaces.append({"source": origen, "target": destino, "value": float(rel["peso"])})

    grafo = {
        "nodes": list(nodos.values()),
        "links": enlaces
    }

    return {
        "embudo": {
            "llamadas": total_llamadas,
            "casos_analizados": casos_analizados,
            "precision_a1": precision_a1,
            "clusters": corrida.get("k_clusters"),
            "entidades_riesgo": entidades_riesgo,
            "entidades_detalle": entidades_detalle,
            "patrones": total_patrones,
            "revision": revision_por_estado,
            "clasificacion": por_clasificacion,
            "omitidos_memoria": corrida.get("omitidos_memoria") or [],
            "memoria_total": memoria_resumen["total"],
            "memoria_evaluados": memoria_resumen["evaluados"],
            "corrida_id": corrida.get("corrida_id"),
        },
        "calidad_a1": calidad_a1,
        "ejecucion": {
            "a1": corrida_a1,
            "a1_ultimo_analisis": ultimo_a1["ultimo"],
            "a1_casos_ultimo_dia": ultimo_a1["ese_dia"],
            "llamadas_pendientes": llamadas_pendientes,
            "a2": {"corrida_id": corrida.get("corrida_id"), "fecha": corrida.get("created_at"),
                   "n_casos": corrida.get("n_casos")},
        },
        "resumen_ejecutivo": {
            "patrones_identificados": total_patrones,
            "hipotesis_propuestas": total_hipotesis,
            "casos_analizados": casos_analizados,
            "total_llamadas": total_llamadas,
            "nivel_confianza": confianza_promedio,
            "precision_a1": precision_a1,
            "hipotesis_pendientes": hipotesis_pendientes,
            "hipotesis_registradas": hipotesis_registradas,
            "corrida_id": corrida_id,
            "corrida_casos": corrida.get("n_casos"),
            "corrida_fecha": corrida.get("created_at"),
        },
        "evidencias_clave": {
            "transcripciones_analizadas": casos_analizados,
            "reclamos_analizados": reclamos_analizados,
            "transacciones_analizadas": total_transacciones,
            "entidades_relacionadas": total_entidades,
        },
        "corrida_id": corrida_id,
        "patrones": patrones,
        "hipotesis": hipotesis,
        "casos_recientes": casos_recientes,
        "grafo": grafo,
        "servidor": {
            "db_host": os.getenv("POSTGRES_HOST", "localhost"),
            "fecha_actualizacion": datetime.now().isoformat()
        }
    }


def listar_casos() -> list:
    """Casos analizados por el Agente 1 (para la tabla del portal)."""
    conn = database.connect()
    try:
        return caso_model.listar_casos_analizados(conn)
    finally:
        conn.close()


def guardar_revision_caso(call_id: int, accion: str, comentario: str, analista: str, tipologia: str = None):
    """Decisión del analista sobre la ficha de un caso (confirmar, corregir, nueva modalidad, comentar)."""
    conn = database.connect()
    try:
        return caso_model.guardar_revision_caso(conn, call_id, accion, comentario, analista, tipologia)
    finally:
        conn.close()


def detalle_patron(patron_id: int):
    """
    Datos del patrón que no vienen en /api/data: sus valores en la corrida anterior (misma entrada
    de Pattern Memory) para mostrar la tendencia, y el historial de decisiones del analista.
    Devuelve None si el patrón no existe.
    """
    conn = database.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT hipotesis_id, memoria_id, corrida_id FROM hipotesis WHERE patron_id = %s;", (patron_id,))
            h = cur.fetchone()
            if h is None:
                return None
            anterior = None
            if h["memoria_id"] is not None:
                cur.execute("""
                    SELECT corrida_id, num_casos, impacto_monto, confianza
                    FROM hipotesis WHERE memoria_id = %s AND corrida_id < %s
                    ORDER BY corrida_id DESC LIMIT 1;
                """, (h["memoria_id"], h["corrida_id"]))
                anterior = cur.fetchone()
            # Decisiones sobre esta hipótesis y sobre el mismo patrón en corridas anteriores
            cur.execute("""
                SELECT r.estado, r.comentario, r.analista, r.created_at, hh.corrida_id
                FROM revisiones_hipotesis r
                JOIN hipotesis hh ON hh.hipotesis_id = r.hipotesis_id
                WHERE r.hipotesis_id = %s OR (%s::int IS NOT NULL AND hh.memoria_id = %s)
                ORDER BY r.created_at DESC;
            """, (h["hipotesis_id"], h["memoria_id"], h["memoria_id"]))
            revisiones = cur.fetchall()
        return {"anterior": anterior, "revisiones": revisiones}
    finally:
        conn.close()


def obtener_configuracion() -> dict:
    """Parámetros de negocio del Agente 2, catálogo de modalidades vigente y últimas corridas de cada agente."""
    conn = database.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT corrida_id, created_at, n_casos, parametros, modelo, prompt_version
                FROM corridas_patrones ORDER BY corrida_id DESC LIMIT 1;
            """)
            corrida_a2 = cur.fetchone()
            cur.execute("""
                SELECT corrida_a1_id, inicio, fin, n_casos, n_fallidos, modelo, prompt_version, version_manual
                FROM corridas_agente1 ORDER BY corrida_a1_id DESC LIMIT 1;
            """)
            corrida_a1 = cur.fetchone()
            cur.execute("""
                SELECT COALESCE(version_manual, 'no registrada') AS version, COUNT(*) AS fichas
                FROM tipificaciones GROUP BY 1 ORDER BY 1;
            """)
            fichas_por_version = cur.fetchall()
        modalidades = [
            {"clave": k, "nombre": v.get("nombre") or k, "definicion": v.get("definicion", ""),
             "accion_recomendada": v.get("accion_recomendada", ""), "riesgo_base": v.get("riesgo_base"),
             "frases_cliente": v.get("frases_cliente", []), "diferenciar_de": v.get("diferenciar_de", {}),
             "equivalencias_poc": v.get("equivalencias_poc", [])}
            for k, v in MANUAL_DATA.get("modalidades", {}).items() if k != "no_identificado"
        ]
        return {
            "parametros": parametros_model.listar_parametros(conn),
            "catalogo": {"version": VERSION_MANUAL, "descripcion": MANUAL_DATA.get("descripcion", ""),
                         "notas_version": MANUAL_DATA.get("notas_version", ""),
                         "no_fraude": MANUAL_DATA.get("no_fraude", []),
                         "reglas": MANUAL_DATA.get("reglas_tipificacion", []),
                         "modalidades": modalidades, "fichas_por_version": fichas_por_version},
            "corrida_a2": corrida_a2,
            "corrida_a1": corrida_a1,
        }
    finally:
        conn.close()


def guardar_parametros(cambios: dict, analista: str) -> dict:
    conn = database.connect()
    try:
        return parametros_model.guardar_parametros(conn, cambios, analista)
    finally:
        conn.close()


def detalle_caso(call_id: int):
    """Entradas y salidas del Agente 1 para una llamada; None si no fue analizada."""
    conn = database.connect()
    try:
        return caso_model.obtener_detalle_caso(conn, call_id)
    finally:
        conn.close()


def _detalle_entidades_riesgo(cur, corrida_id) -> list:
    """
    Terminales de riesgo de la corrida: su evidencia estadística (guardada en la hipótesis del
    patrón que las usa), qué tipo de comercio son y dónde están, y el patrón asociado.
    """
    cur.execute("""
        SELECT DISTINCT origen AS entidad_id
        FROM relaciones_entidades
        WHERE corrida_id = %s AND origen_tipo = 'terminal'
        ORDER BY 1;
    """, (corrida_id,))
    nombres = [r["entidad_id"] for r in cur.fetchall()]

    cur.execute("""
        SELECT p.patron_id, p.titulo, h.hipotesis_id, h.estado, h.evidencia -> 'entidad_de_riesgo' AS entidad
        FROM patrones p
        JOIN hipotesis h ON h.patron_id = p.patron_id
        WHERE COALESCE(p.vigente, TRUE) AND h.evidencia ? 'entidad_de_riesgo';
    """)
    por_entidad = {r["entidad"]["entidad_id"]: r for r in cur.fetchall()}

    detalle = []
    for nombre in nombres:
        cur.execute("""
            SELECT
                (SELECT merchant_category FROM card_transactions WHERE terminal_id = %s
                 GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1) AS comercio,
                (SELECT ciudad_comercio FROM card_transactions WHERE terminal_id = %s
                 GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1) AS ciudad,
                (SELECT canal FROM card_transactions WHERE terminal_id = %s
                 GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1) AS canal,
                (SELECT COUNT(DISTINCT merchant_category) FROM card_transactions WHERE terminal_id = %s) AS n_rubros;
        """, (nombre, nombre, nombre, nombre))
        ubicacion = cur.fetchone()
        fila = por_entidad.get(nombre)
        detalle.append({
            "entidad_id": nombre,
            **(fila["entidad"] if fila else {}),
            "comercio": ubicacion["comercio"] if ubicacion["n_rubros"] == 1 else f"Varios rubros ({ubicacion['n_rubros']})",
            "ciudad": ubicacion["ciudad"],
            "canal": ubicacion["canal"],
            "patron_id": fila["patron_id"] if fila else None,
            "patron_titulo": fila["titulo"] if fila else None,
            "estado_hipotesis": fila["estado"] if fila else None,
        })
    return detalle


# ---------------------------------------------------------------------------
# Exportación (CSV que abre directamente en Excel)
# ---------------------------------------------------------------------------
def exportar(tipo: str, patron_id: int = None) -> tuple:
    """
    Devuelve (nombre_archivo, encabezados, filas) para tipo = casos, patrones o decisiones.
    Con patron_id, los casos se limitan a los que respaldan ese patrón.
    """
    conn = database.connect()
    try:
        if tipo == "casos":
            casos = caso_model.listar_casos_analizados(conn)
            nombre = "reclamos_analizados.csv"
            if patron_id is not None:
                with conn.cursor() as cur:
                    cur.execute("SELECT casos_asociados FROM patrones WHERE patron_id = %s;", (patron_id,))
                    fila = cur.fetchone()
                if fila is None:
                    raise ValueError(f"No existe el patrón {patron_id}")
                ids = set(fila["casos_asociados"] or [])
                casos = [c for c in casos if c["call_id"] in ids]
                nombre = f"casos_patron_{patron_id}.csv"
            filas = [
                [c["call_id"], c["modalidad"], c["modalidad_real"],
                 "Sí" if c["correcta"] else ("No" if c["correcta"] is False else ""),
                 c["confianza_score"] if c["confianza_score"] is not None else c["confianza"],
                 c["variante_nombre"], c["estado_revision"], c["tipologia_analista"] or "",
                 c["estado_ficha"], c["nivel_riesgo"], "; ".join(c["riesgo_razones"]),
                 c["amount"], c["merchant_category"], c["canal_txn"], c["modo_entrada"],
                 c["ciudad_comercio"], c["ciudad_cliente"], c["canal_reclamo"]]
                for c in casos
            ]
            return nombre, [
                "Llamada", "Tipo de fraude (IA)", "Modalidad real", "Acierto", "Confianza IA",
                "Variante de la ficha", "Revisión del analista", "Tipología del analista",
                "Estado de la ficha", "Nivel de riesgo", "Motivos del riesgo", "Monto (S/)", "Comercio",
                "Canal", "Modo de entrada", "Ciudad del comercio", "Ciudad del cliente", "Canal de reclamo",
            ], filas

        with conn.cursor() as cur:
            if tipo == "patrones":
                cur.execute("""
                    SELECT p.patron_id, p.titulo, p.modalidad_asociada, p.num_casos, p.impacto_total,
                           h.confianza, h.prioridad, h.clasificacion, h.estado, h.analista, h.comentario_analista,
                           h.evidencia -> 'entidad_de_riesgo' ->> 'entidad_id' AS comercio_sospechoso,
                           h.enunciado, p.accion_recomendada, p.casos_asociados, p.corrida_id
                    FROM patrones p JOIN hipotesis h ON h.patron_id = p.patron_id
                    WHERE COALESCE(p.vigente, TRUE)
                    ORDER BY p.patron_id;
                """)
                filas = [
                    [f"P{i:02d}", r["titulo"], r["modalidad_asociada"], r["num_casos"], r["impacto_total"],
                     r["confianza"], r["prioridad"], r["clasificacion"], r["estado"], r["analista"],
                     r["comentario_analista"], r["comercio_sospechoso"], r["enunciado"], r["accion_recomendada"],
                     " ".join(f"#{x}" for x in (r["casos_asociados"] or [])), r["corrida_id"]]
                    for i, r in enumerate(cur.fetchall(), 1)
                ]
                return "patrones_e_hipotesis.csv", [
                    "Patrón", "Título", "Tipo de fraude principal", "Reclamos", "Impacto (S/)", "Confianza (%)",
                    "Prioridad", "Memoria", "Estado", "Analista", "Comentario del analista",
                    "Comercio sospechoso", "Hipótesis", "Recomendación", "Reclamos que lo respaldan", "Corrida",
                ], filas

            if tipo == "decisiones":
                cur.execute("""
                    SELECT r.created_at, r.hipotesis_id, p.titulo, r.estado, r.analista, r.comentario, h.corrida_id
                    FROM revisiones_hipotesis r
                    JOIN hipotesis h ON h.hipotesis_id = r.hipotesis_id
                    JOIN patrones p ON p.patron_id = h.patron_id
                    ORDER BY r.created_at DESC;
                """)
                filas = [[r["created_at"], r["hipotesis_id"], r["titulo"], r["estado"], r["analista"],
                          r["comentario"], r["corrida_id"]] for r in cur.fetchall()]
                return "decisiones_del_analista.csv", [
                    "Fecha", "Hipótesis", "Patrón", "Decisión", "Analista", "Comentario", "Corrida",
                ], filas
    finally:
        conn.close()
    raise ValueError(f"Tipo de exportación no válido: {tipo}")
