"""
Datos del Agente 2: universo de casos tipificados, transacciones con tarjeta y
persistencia versionada de corridas, patrones, hipótesis y relaciones.
"""
import json

import pandas as pd


# ---------------------------------------------------------------------------
# Paso 1: Carga de Datos Enriquecidos desde PostgreSQL
# ---------------------------------------------------------------------------
def cargar_universo_enriquecido(conn, ventana_dias: int = 0, excluidas=()) -> pd.DataFrame:
    """
    Fichas del Agente 1 que entran a la corrida: las de los últimos ventana_dias días
    (0 = todas) y sin las modalidades excluidas por negocio.
    """
    query = """
        SELECT 
            t.tipificacion_id,
            t.call_id,
            -- La tipología fijada por el analista en el portal manda sobre la de la IA
            COALESCE(t.tipologia_analista, t.ai_tipificacion, t.modalidad_predicha, 'no_identificado') AS modalidad_predicha,
            CASE WHEN t.estado_revision = 'Nueva modalidad' THEN 'Nueva'
                 ELSE COALESCE(t.ai_estado_tipificacion, 'Existente') END AS ai_estado_tipificacion,
            COALESCE(t.ai_resumen_caso, t.justificacion, '') AS ai_resumen_caso,
            COALESCE(t.ai_resumen_caso_tags, '') AS ai_resumen_caso_tags,
            COALESCE(t.ai_insights, t.justificacion, '') AS ai_insights,
            COALESCE(t.ai_insights_tags, '') AS ai_insights_tags,
            COALESCE(t.ai_blindaje, '') AS ai_blindaje,
            COALESCE(t.ai_blindaje_tags, '') AS ai_blindaje_tags,
            COALESCE(t.confianza, 'media') AS confianza,
            t.confianza_score,
            l.canal AS call_canal,
            l.fecha_llamada,
            l.duracion_segundos,
            ct.card_txn_id,
            ct.card_id,
            ct.txn_date,
            COALESCE(ct.merchant_category, 'Desconocido') AS merchant_category,
            COALESCE(ct.amount, 0) AS amount,
            COALESCE(ct.canal, 'Desconocido') AS canal_txn,
            COALESCE(ct.modo_entrada, 'Desconocido') AS modo_entrada,
            COALESCE(ct.ciudad_comercio, 'Desconocido') AS ciudad_comercio,
            ct.terminal_id,
            COALESCE(c.card_type, 'Desconocido') AS card_type,
            COALESCE(c.credit_limit, 0) AS credit_limit,
            COALESCE(cu.city, 'Desconocido') AS customer_city,
            COALESCE(cu.occupation, 'Desconocido') AS customer_occupation
        FROM tipificaciones t
        JOIN llamadas l ON l.call_id = t.call_id
        LEFT JOIN card_transactions ct ON ct.card_txn_id = l.card_txn_id
        LEFT JOIN cards c ON c.card_id = ct.card_id
        LEFT JOIN customers cu ON cu.customer_id = l.customer_id
        WHERE (%(ventana)s = 0 OR l.fecha_llamada >= NOW() - make_interval(days => %(ventana)s))
          -- Fichas 2 (no fraude) y 5 (ilegible) no entran al agrupamiento (propuesta, láminas 16 y 69)
          AND COALESCE(t.ai_situacion, 'fraude') = 'fraude'
          AND NOT (COALESCE(t.tipologia_analista, t.ai_tipificacion, t.modalidad_predicha, 'no_identificado')
                   = ANY(%(excluidas)s))
        ORDER BY t.tipificacion_id;
    """
    with conn.cursor() as cur:
        cur.execute(query, {"ventana": int(ventana_dias or 0), "excluidas": list(excluidas)})
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)


def cargar_transacciones(conn) -> pd.DataFrame:
    """Todas las transacciones con tarjeta: historial de cada cliente y línea base legítima."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT card_txn_id, card_id, txn_date, amount, is_fraud,
                   canal, modo_entrada, terminal_id, ciudad_comercio
            FROM card_transactions
            ORDER BY card_id, txn_date;
        """)
        df = pd.DataFrame(cur.fetchall())
    df["amount"] = df["amount"].astype(float)
    return df


# ---------------------------------------------------------------------------
# Paso 8: Persistencia versionada y Visualización
# ---------------------------------------------------------------------------
def guardar_corrida(conn, df: pd.DataFrame, best_k: int, best_score: float,
                    patrones: list, relaciones: list, omitidos: list = None,
                    modelo: str = None, prompt_version: str = None, parametros: dict = None) -> int:
    """
    Guarda una corrida nueva en una sola transacción. Los patrones e hipótesis anteriores
    se marcan como no vigentes en vez de borrarse, así A3-A5 y la revisión humana conservan
    su historia.
    """
    try:
        corrida_id = _insertar_corrida(conn, df, best_k, best_score, patrones, relaciones,
                                       omitidos or [], modelo, prompt_version, parametros)
        conn.commit()
        return corrida_id
    except Exception:
        conn.rollback()
        raise


def _insertar_corrida(conn, df, best_k, best_score, patrones, relaciones,
                      omitidos=(), modelo=None, prompt_version=None, parametros=None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO corridas_patrones (n_casos, k_clusters, silhouette, omitidos_memoria, modelo, prompt_version,
                                           parametros)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING corrida_id;
            """,
            (len(df), best_k, round(float(best_score), 3), json.dumps(list(omitidos), ensure_ascii=False),
             modelo, prompt_version, json.dumps(parametros or {}, ensure_ascii=False)),
        )
        corrida_id = cur.fetchone()["corrida_id"]

        cur.execute("UPDATE patrones SET vigente = FALSE WHERE vigente IS DISTINCT FROM FALSE;")
        cur.execute("UPDATE hipotesis SET vigente = FALSE WHERE vigente IS DISTINCT FROM FALSE;")

        for p in patrones:
            llm = p["llm"]
            cur.execute(
                """
                INSERT INTO patrones (
                    titulo, modalidad_asociada, descripcion, evidencia, num_casos, regla_propuesta,
                    ai_razonamiento_oculto, analisis_reglas, distribuciones, analisis_tipificaciones,
                    accion_recomendada, cluster_id, clusters_origen, tasa_sospecha, impacto_total,
                    casos_asociados, raw_payload, corrida_id, vigente
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, TRUE
                ) RETURNING patron_id;
                """,
                (
                    p["titulo"],
                    p["modalidad_asociada"],
                    llm.get("descripcion"),
                    p["evidencia_texto"],
                    p["num_casos"],
                    llm.get("regla_propuesta"),
                    llm.get("ai_razonamiento_oculto"),
                    llm.get("analisis_reglas"),
                    p["distribuciones"],
                    llm.get("analisis_tipificaciones"),
                    llm.get("accion_recomendada"),
                    p["cluster_ids"][0],
                    json.dumps(p["cluster_ids"]),
                    p["tasa_sospecha"],
                    p["impacto_total"],
                    json.dumps(p["casos_asociados"]),
                    json.dumps(llm, ensure_ascii=False),
                    corrida_id,
                ),
            )
            patron_id = cur.fetchone()["patron_id"]

            mem = p.get("memoria") or {}
            memoria_id = _registrar_en_memoria(cur, corrida_id, p, mem, modelo, prompt_version) if mem else None

            cur.execute(
                """
                INSERT INTO hipotesis (
                    corrida_id, patron_id, enunciado, modalidad, confianza, confianza_detalle,
                    impacto_monto, nivel_impacto, num_casos, casos_asociados, evidencia, vigente,
                    memoria_id, clasificacion, casos_nuevos, decision_heredada, estado, comentario_analista,
                    prioridad, prioridad_detalle
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    corrida_id,
                    patron_id,
                    p["enunciado_hipotesis"],
                    p["modalidad_asociada"],
                    p["confianza"],
                    json.dumps(p["confianza_detalle"]),
                    p["impacto_total"],
                    p["nivel_impacto"],
                    p["num_casos"],
                    json.dumps(p["casos_asociados"]),
                    json.dumps(p["evidencia"], ensure_ascii=False),
                    memoria_id,
                    mem.get("clasificacion"),
                    mem.get("casos_nuevos"),
                    bool(mem.get("decision_heredada")),
                    mem.get("estado_inicial") or "Pendiente revisión",
                    mem.get("comentario_inicial"),
                    p.get("prioridad"),
                    json.dumps(p.get("prioridad_detalle")) if p.get("prioridad_detalle") else None,
                ),
            )

        for r in relaciones:
            cur.execute(
                """
                INSERT INTO relaciones_entidades (
                    corrida_id, origen_tipo, origen, destino_tipo, destino, peso, casos, clusters
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    corrida_id, r["origen_tipo"], r["origen"], r["destino_tipo"], r["destino"],
                    r["peso"], json.dumps(r["casos"]), json.dumps(r["clusters"]),
                ),
            )

    return corrida_id


def obtener_patrones_vigentes(conn) -> list:
    """Patrones de la corrida vigente, con la hipótesis asociada."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.patron_id, p.titulo, p.modalidad_asociada, p.clusters_origen, p.num_casos,
                p.impacto_total, p.descripcion, p.evidencia, p.corrida_id,
                p.distribuciones, p.analisis_reglas, p.accion_recomendada, p.regla_propuesta,
                h.enunciado AS hipotesis, h.confianza AS confianza_hipotesis,
                h.nivel_impacto, h.estado AS estado_hipotesis,
                h.clasificacion, h.casos_nuevos, h.decision_heredada
            FROM patrones p
            LEFT JOIN hipotesis h ON h.patron_id = p.patron_id
            WHERE COALESCE(p.vigente, TRUE)
            ORDER BY p.patron_id;
        """)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Pattern Memory
# ---------------------------------------------------------------------------
def _registrar_en_memoria(cur, corrida_id, p, mem, modelo, prompt_version) -> int:
    """Crea la entrada de memoria de un patrón nuevo o actualiza la del patrón conocido."""
    rasgos = json.dumps(p["evidencia"].get("rasgos_distintivos", []), ensure_ascii=False)
    if mem.get("memoria_id"):
        cur.execute(
            """
            UPDATE memoria_patrones
            SET titulo = %s, rasgos = %s, casos_vistos = %s, num_casos = %s, impacto_total = %s,
                confianza = %s, ultima_corrida = %s, veces_detectado = veces_detectado + 1,
                modelo = %s, prompt_version = %s, updated_at = NOW()
            WHERE memoria_id = %s
            RETURNING memoria_id;
            """,
            (p["titulo"], rasgos, json.dumps(mem["casos_vistos"]), p["num_casos"], p["impacto_total"],
             p["confianza"], corrida_id, modelo, prompt_version, mem["memoria_id"]),
        )
    else:
        cur.execute(
            """
            INSERT INTO memoria_patrones (
                firma, tipo, modalidad, entidad_id, titulo, rasgos, casos_vistos, num_casos,
                impacto_total, confianza, primera_corrida, ultima_corrida, modelo, prompt_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING memoria_id;
            """,
            (mem["firma"], mem["tipo"], p["modalidad_asociada"], mem["entidad_id"], p["titulo"], rasgos,
             json.dumps(mem["casos_vistos"]), p["num_casos"], p["impacto_total"], p["confianza"],
             corrida_id, corrida_id, modelo, prompt_version),
        )
    return cur.fetchone()["memoria_id"]


def cargar_memoria(conn) -> list:
    """Todas las entradas de Pattern Memory con la última decisión del analista."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM memoria_patrones ORDER BY memoria_id;")
        return cur.fetchall()


def adoptar_vigentes_en_memoria(conn, min_casos_patron: int = 5) -> int:
    """
    Incorpora a la memoria las hipótesis vigentes que aún no tienen entrada (las creadas antes
    de Pattern Memory) como primera detección. Es idempotente. Devuelve cuántas incorporó.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT h.hipotesis_id, h.estado, h.comentario_analista, h.analista, h.fecha_revision,
                   h.confianza, h.evidencia, h.num_casos, h.impacto_monto, h.corrida_id,
                   p.titulo, p.modalidad_asociada, p.casos_asociados, p.raw_payload->>'entidad_id' AS entidad_id
            FROM hipotesis h
            JOIN patrones p ON p.patron_id = h.patron_id
            WHERE h.vigente AND h.memoria_id IS NULL;
        """)
        pendientes = cur.fetchall()
        for h in pendientes:
            entidad_id = h["entidad_id"]
            tipo, firma = (("entidad", f"entidad:{entidad_id}") if entidad_id
                           else ("cluster", f"cluster:{h['modalidad_asociada']}"))
            cur.execute(
                """
                INSERT INTO memoria_patrones (
                    firma, tipo, modalidad, entidad_id, titulo, rasgos, casos_vistos, num_casos,
                    impacto_total, confianza, primera_corrida, ultima_corrida, estado, comentario,
                    analista, fecha_decision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING memoria_id;
                """,
                (firma, tipo, h["modalidad_asociada"], entidad_id, h["titulo"],
                 json.dumps((h["evidencia"] or {}).get("rasgos_distintivos", []), ensure_ascii=False),
                 json.dumps(h["casos_asociados"] or []), h["num_casos"], h["impacto_monto"], h["confianza"],
                 h["corrida_id"], h["corrida_id"], h["estado"], h["comentario_analista"], h["analista"],
                 h["fecha_revision"]),
            )
            memoria_id = cur.fetchone()["memoria_id"]
            cur.execute(
                """
                UPDATE hipotesis
                SET memoria_id = %s, clasificacion = COALESCE(clasificacion, %s), casos_nuevos = COALESCE(casos_nuevos, num_casos)
                WHERE hipotesis_id = %s;
                """,
                (memoria_id, "Bajo umbral" if (h["num_casos"] or 0) < min_casos_patron else "Nuevo", h["hipotesis_id"]),
            )
    conn.commit()
    return len(pendientes)
