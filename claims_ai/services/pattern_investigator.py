"""
Agente 2 - Pattern Investigator / Cerebro Macro (POC Claims AI v3.0)
Detecta anomalías estadísticas y clusters de comportamiento fraudulento a partir
del universo enriquecido de A1. Identifica los patrones sistémicos más críticos
y emite un reporte maestro con hallazgos forenses y reglas de monitoreo.

Pipeline v3.2:
0. Anomalías y entidades de riesgo (deterministas): cada caso se compara con el historial legítimo
   de su tarjeta (monto atípico, ciudad nueva, canal nuevo, lectura por banda/fallback) y con la
   línea base de compras legítimas. Se buscan terminales o comercios que concentran víctimas
   (punto de compromiso) o cobros (destino común) por encima de lo esperable por azar
   (prueba de Poisson con corrección de Bonferroni).
1. Feature Engineering en cuatro bloques ponderados (PESOS_BLOQUES): texto y tags de A1 (TF-IDF,
   100 términos), modalidad, contexto (comercio, ciudad, tipo de tarjeta, franja horaria) y monto.
   Cada bloque se normaliza a varianza total 1 para que ninguno domine por su escala.
2. Reducción Dimensional con PCA (hasta 15 componentes).
3. Clustering K-Means adaptativo con selección automática de k óptimo mediante Silhouette Score.
4. Análisis Pre-LLM determinista sin consumo de tokens:
   - P1: Detección de activación parcial y concentración de reglas/vectores.
   - P2: Rangos operativos y percentiles (P25, P50, P75) por canal y comercio.
   - P3: Inconsistencias de tipificación por firmas compartidas.
   - Rasgos distintivos: comercios, ciudades, tarjetas, horarios o montos sobrerrepresentados
     en el cluster frente al total (lift >= 1.5, mínimo 3 casos).
5. Síntesis Forense LLM estructurada con Claude Sonnet: el LLM solo redacta (título, descripción,
   hipótesis, regla) y dice qué clusters cubre cada patrón.
6. Consolidación determinista: num_casos, impacto, percentiles, evidencia y confianza de la
   hipótesis se recalculan en código a partir de los casos reales (el LLM no escribe cifras).
7. Relaciones entre entidades (modalidad, comercio, ciudad, canal de reclamo, tipo de tarjeta).
8. Persistencia versionada: cada ejecución crea una corrida nueva y marca la anterior como no
   vigente (no se borra historia ni las validaciones/reglas de A3-A5).
"""
import json
import re
from datetime import datetime

import anthropic
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score

from claims_ai.config import MODELO_CLAUDE
from claims_ai.models.manual_model import MODALIDADES_VALIDAS


# ---------------------------------------------------------------------------
# Paso 1b: Anomalías por caso y entidades de riesgo (deterministas, sin LLM)
# ---------------------------------------------------------------------------
HISTORIAL_MINIMO = 5          # transacciones legítimas previas para juzgar si algo es "nuevo" o "atípico"


MODOS_RIESGOSOS = {"BANDA", "FALLBACK"}


SEÑALES_ANOMALIA = {
    "monto_atipico": "monto mayor a la media + 2 desviaciones del historial de la tarjeta",
    "ciudad_nueva": "compra presencial en una ciudad donde la tarjeta nunca había comprado",
    "canal_nuevo": "canal (POS / INT / APP) que la tarjeta nunca había usado",
    "modo_riesgoso": "lectura por banda magnética o fallback (clonable)",
}


VENTANA_COMPROMISO_DIAS = 30  # compras previas al fraude que se revisan como posible punto de compromiso


MIN_CASOS_ENTIDAD = 3


LIFT_MIN_ENTIDAD = 5.0


# Se revisan cientos de terminales: sin corrección, alguna coincidencia de 3 víctimas es esperable.
# Una entidad se reporta solo si P(>= n casos por azar) * nº de terminales revisados < ALFA (Bonferroni).
ALFA_ENTIDAD = 0.01


def marcar_anomalias(tx: pd.DataFrame) -> pd.DataFrame:
    """
    Marca en cada transacción las señales de anomalía frente al historial legítimo previo
    de su propia tarjeta. Solo las transacciones legítimas alimentan el historial.
    """
    registros = []
    for _, g in tx.groupby("card_id", sort=False):
        montos, ciudades, canales = [], set(), set()
        for fila in g.itertuples():
            con_historial = len(montos) >= HISTORIAL_MINIMO
            media, desv = (np.mean(montos), np.std(montos)) if montos else (0.0, 0.0)
            registros.append({
                "card_txn_id": fila.card_txn_id,
                "monto_atipico": bool(con_historial and desv > 0 and fila.amount > media + 2 * desv),
                "ciudad_nueva": bool(con_historial and fila.ciudad_comercio != "Online"
                                     and fila.ciudad_comercio not in ciudades),
                "canal_nuevo": bool(con_historial and fila.canal not in canales),
                "modo_riesgoso": fila.modo_entrada in MODOS_RIESGOSOS,
                "con_historial": con_historial,
            })
            if not fila.is_fraud:
                montos.append(fila.amount)
                ciudades.add(fila.ciudad_comercio)
                canales.add(fila.canal)
    return tx.merge(pd.DataFrame(registros), on="card_txn_id")


def linea_base_legitima(tx_marcadas: pd.DataFrame) -> dict:
    """% de transacciones legítimas (con historial suficiente) que presentan cada señal."""
    legit = tx_marcadas[(~tx_marcadas["is_fraud"]) & tx_marcadas["con_historial"]]
    return {s: round(100.0 * legit[s].mean(), 1) for s in SEÑALES_ANOMALIA}


def detectar_entidades_riesgo(casos: pd.DataFrame, tx: pd.DataFrame) -> list:
    """
    Terminales o comercios que concentran casos mucho más de lo esperable por azar:
      - punto_de_compromiso: donde varias víctimas compraron en los días previos al fraude
        (típico de skimming o filtración de datos en un comercio).
      - destino_comun: donde se cobraron varios fraudes (pasarela o comercio usado para cobrar).
    El valor esperado se calcula con la participación de cada terminal en las compras legítimas,
    y la significancia con una prueba de Poisson corregida por el número de terminales revisados.
    """
    legit = tx[~tx["is_fraud"]]
    participacion = legit["terminal_id"].value_counts() / len(legit)
    entidades = []

    # Punto de compromiso: compras legítimas previas de cada víctima dentro de la ventana
    visitas, compras_en_ventana = {}, 0
    por_tarjeta = {k: g for k, g in legit.groupby("card_id")}
    for caso in casos.itertuples():
        g = por_tarjeta.get(caso.card_id)
        if g is None or pd.isna(caso.txn_date):
            continue
        # Se excluyen compras en la ciudad donde ocurrió el fraude: coincidir con el lugar del
        # fraude (p. ej. durante un viaje) no indica dónde se comprometió la tarjeta.
        ventana = g[(g["txn_date"] < caso.txn_date)
                    & (g["txn_date"] >= caso.txn_date - pd.Timedelta(days=VENTANA_COMPROMISO_DIAS))
                    & ((g["ciudad_comercio"] != caso.ciudad_comercio) | (g["ciudad_comercio"] == "Online"))]
        compras_en_ventana += len(ventana)
        for terminal in set(ventana["terminal_id"].dropna()):
            visitas.setdefault(terminal, {}).setdefault(int(caso.card_id), []).append(int(caso.call_id))
    for terminal, por_tarjeta_victima in visitas.items():
        # Se cuentan víctimas (tarjetas distintas), no reclamos: una tarjeta puede tener varios
        call_ids = [c for ids in por_tarjeta_victima.values() for c in ids]
        esperado = compras_en_ventana * participacion.get(terminal, 0.0)
        entidades.append(("punto_de_compromiso", terminal, call_ids, esperado, len(visitas),
                          len(por_tarjeta_victima)))

    # Destino común: terminal donde ocurrió la transacción reclamada
    destinos = casos.dropna(subset=["terminal_id"]).groupby("terminal_id")
    for terminal, g in destinos:
        esperado = len(casos) * participacion.get(terminal, 0.0)
        entidades.append(("destino_comun", terminal, [int(x) for x in g["call_id"]], esperado, destinos.ngroups,
                          g["card_id"].nunique()))

    resultado = []
    for tipo, terminal, call_ids, esperado, n_pruebas, n_victimas in entidades:
        lift = n_victimas / max(esperado, 0.01)
        # Probabilidad de ver tantas víctimas por azar (con un piso para terminales sin uso legítimo)
        p_valor = float(poisson.sf(n_victimas - 1, max(esperado, 0.01)))
        p_corregido = min(1.0, p_valor * n_pruebas)
        if n_victimas < MIN_CASOS_ENTIDAD or lift < LIFT_MIN_ENTIDAD or p_corregido >= ALFA_ENTIDAD:
            continue
        sub = casos[casos["call_id"].isin(call_ids)]
        clientes_en_terminal = legit[legit["terminal_id"] == terminal]["card_id"].nunique()
        resultado.append({
            "entidad_id": terminal,
            "tipo": tipo,
            "n_casos": len(call_ids),
            "n_victimas": int(n_victimas),
            "casos": sorted(call_ids),
            "esperado_por_azar": round(float(esperado), 2),
            "lift": round(float(min(lift, 999.0)), 1),
            "p_valor_corregido": float(f"{p_corregido:.2g}"),
            "modalidades_a1": {str(k): int(v) for k, v in sub["modalidad_predicha"].value_counts().items()},
            "tarjetas_que_usaron_el_terminal": int(clientes_en_terminal),
        })
    return sorted(resultado, key=lambda e: -e["n_casos"])


def enriquecer_con_anomalias(df: pd.DataFrame, tx_marcadas: pd.DataFrame) -> pd.DataFrame:
    """Agrega a cada caso sus señales de anomalía y el total de señales."""
    señales = tx_marcadas[["card_txn_id"] + list(SEÑALES_ANOMALIA)]
    df = df.merge(señales, on="card_txn_id", how="left")
    for s in SEÑALES_ANOMALIA:
        df[s] = df[s].fillna(False).astype(bool)
    df["n_anomalias"] = df[list(SEÑALES_ANOMALIA)].sum(axis=1)
    return df


def resumen_anomalias(cdf: pd.DataFrame, base: dict) -> dict:
    """% de casos del grupo con cada señal, frente al % en transacciones legítimas."""
    return {
        s: {"pct_casos": round(100.0 * cdf[s].mean(), 1), "pct_legitimas": base[s]}
        for s in SEÑALES_ANOMALIA if cdf[s].any()
    }


# ---------------------------------------------------------------------------
# Paso 2: Feature Engineering Determinista + TF-IDF
# ---------------------------------------------------------------------------
def calcular_franja_horaria(fecha_val) -> str:
    if not fecha_val or pd.isna(fecha_val):
        return "desconocido"
    if isinstance(fecha_val, str):
        try:
            hora = datetime.fromisoformat(fecha_val).hour
        except Exception:
            return "tarde"
    elif hasattr(fecha_val, "hour"):
        hora = fecha_val.hour
    else:
        return "tarde"

    if 0 <= hora < 6:
        return "madrugada"
    elif 6 <= hora < 12:
        return "manana"
    elif 12 <= hora < 18:
        return "tarde"
    else:
        return "noche"


# Peso de cada bloque de variables en el clustering. Cada bloque se normaliza a
# varianza total 1 antes de aplicar el peso; sin esto, el monto y las categorías
# con muchos valores (ciudad, comercio) dominaban y los clusters salían por monto.
# Calibrado sobre 56 casos: pureza de modalidad 30% -> 100%, silhouette 0.14 -> 0.47.
PESOS_BLOQUES = {"texto": 2.0, "modalidad": 2.0, "contexto": 1.0, "monto": 0.5}


COLS_CONTEXTO = ["comercio_agrupado", "customer_city", "card_type", "franja_horaria", "canal_txn", "modo_entrada"]


def _bloque_normalizado(M) -> np.ndarray:
    """Centra el bloque y lo escala para que su varianza total sea 1."""
    M = np.asarray(M, dtype=float)
    M = M - M.mean(axis=0)
    varianza_total = M.var(axis=0).sum()
    return M / np.sqrt(varianza_total) if varianza_total > 0 else M


def pipeline_feature_engineering(df: pd.DataFrame):
    """
    Construye la matriz matemática con cuatro bloques ponderados (PESOS_BLOQUES):
    texto de A1 (TF-IDF), modalidad, contexto transaccional y monto.
    El canal de reclamo no participa: es por dónde reportó el cliente, no un rasgo del fraude.
    """
    # 1. Variables Derivadas
    df["franja_horaria"] = df["txn_date"].fillna(df["fecha_llamada"]).apply(calcular_franja_horaria)

    # 2. Top-N Encoding para comercios (Top 15 para dataset mediano)
    top_merchants = df["merchant_category"].value_counts().nlargest(15).index
    df["comercio_agrupado"] = df["merchant_category"].apply(lambda x: x if x in top_merchants else "other")

    # 3. TF-IDF sobre texto semántico y tags de A1
    df["texto_completo"] = (
        df["ai_resumen_caso"] + " " +
        df["ai_resumen_caso_tags"] + " " +
        df["ai_insights"] + " " +
        df["ai_insights_tags"] + " " +
        df["ai_blindaje"] + " " +
        df["ai_blindaje_tags"]
    ).fillna("")

    # Vectorizar hasta 100 términos semánticos
    tfidf = TfidfVectorizer(max_features=100, min_df=1, ngram_range=(1, 2))
    matriz_tfidf = tfidf.fit_transform(df["texto_completo"]).toarray()

    # 4. Bloques categóricos y monto (log para reducir el peso de los montos extremos)
    matriz_modalidad = pd.get_dummies(df["modalidad_predicha"]).astype(float).values
    matriz_contexto = pd.get_dummies(df[COLS_CONTEXTO]).astype(float).values
    matriz_monto = np.log1p(df["amount"].astype(float).values).reshape(-1, 1)

    # 5. Concatenar los bloques normalizados y ponderados
    X_full = np.hstack([
        PESOS_BLOQUES["texto"] * _bloque_normalizado(matriz_tfidf),
        PESOS_BLOQUES["modalidad"] * _bloque_normalizado(matriz_modalidad),
        PESOS_BLOQUES["contexto"] * _bloque_normalizado(matriz_contexto),
        PESOS_BLOQUES["monto"] * _bloque_normalizado(matriz_monto),
    ])
    return X_full, df


# ---------------------------------------------------------------------------
# Paso 3: PCA + K-Means Adaptativo con Silhouette Score
# ---------------------------------------------------------------------------
def clustering_k_means_optimo(X: np.ndarray, df: pd.DataFrame):
    """
    Reduce dimensiones con PCA y evalúa múltiples valores de k seleccionando
    el k óptimo con el mayor Silhouette Score.
    """
    n_samples, n_features = X.shape
    if n_samples < 4:
        df["cluster"] = 0
        return df, 1, 0.0

    # PCA a 15 componentes (o menos si hay menos muestras)
    n_comp = min(15, n_samples - 1, n_features)
    pca = PCA(n_components=n_comp, random_state=42)
    X_pca = pca.fit_transform(X)

    # Rango de k adaptativo
    min_k = 2
    max_k = min(8, max(3, n_samples // 3))

    best_k = min_k
    best_score = -1.0
    best_labels = None

    from sklearn.cluster import KMeans

    for k in range(min_k, max_k + 1):
        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = kmeans.fit_predict(X_pca)
        
        # Validar si hay más de 1 cluster con elementos
        if len(set(labels)) > 1:
            score = silhouette_score(X_pca, labels)
            if score > best_score:
                best_score = score
                best_k = k
                best_labels = labels

    if best_labels is None:
        best_k = 2
        kmeans = KMeans(n_clusters=2, random_state=42, n_init=10)
        best_labels = kmeans.fit_predict(X_pca)
        best_score = 0.0

    df["cluster"] = best_labels
    return df, best_k, best_score


# ---------------------------------------------------------------------------
# Paso 4: Análisis Pre-LLM Determinista (P1, P2, P3)
# ---------------------------------------------------------------------------
# Equivalencia para casos tipificados antes de que A1 emitiera confianza numérica
CONFIANZA_POR_ETIQUETA = {"alta": 85.0, "media": 60.0, "baja": 35.0}


def agregar_confianza_a1(df: pd.DataFrame) -> pd.DataFrame:
    """Columna conf_a1 (0-100): confianza_score de A1 o, si falta, su equivalente por etiqueta."""
    fallback = df["confianza"].map(CONFIANZA_POR_ETIQUETA).fillna(60.0)
    df["conf_a1"] = pd.to_numeric(df["confianza_score"], errors="coerce").fillna(fallback).astype(float)
    return df


# Un rasgo es distintivo de un grupo si aparece en al menos MIN_CASOS_RASGO casos
# y su proporción en el grupo es LIFT_MIN veces (o más) la del universo completo.
MIN_CASOS_RASGO = 3


LIFT_MIN = 1.5


COLS_RASGOS = {
    "comercio": "merchant_category",
    "ciudad": "customer_city",
    "tipo_tarjeta": "card_type",
    "franja_horaria": "franja_horaria",
    "canal_transaccion": "canal_txn",
    "modo_entrada": "modo_entrada",
    "ciudad_comercio": "ciudad_comercio",
}


def rasgos_distintivos(cdf: pd.DataFrame, universo: pd.DataFrame) -> list:
    """
    Rasgos sobrerrepresentados en el grupo frente al universo, con su lift.
    Evita que el LLM presente como concentración algo que solo refleja la distribución general.
    """
    rasgos = []
    n, n_u = len(cdf), len(universo)
    for nombre, col in COLS_RASGOS.items():
        conteos_u = universo[col].value_counts()
        for valor, casos in cdf[col].value_counts().items():
            if casos < MIN_CASOS_RASGO:
                continue
            lift = (casos / n) / (conteos_u[valor] / n_u)
            if lift >= LIFT_MIN:
                rasgos.append({
                    "rasgo": nombre, "valor": str(valor), "casos": int(casos),
                    "pct_grupo": round(100.0 * casos / n, 1),
                    "pct_universo": round(100.0 * conteos_u[valor] / n_u, 1),
                    "lift": round(float(lift), 2),
                })

    # Monto: el grupo es de montos bajos/altos si su mediana cae fuera del P25-P75 del universo
    montos_u = universo["amount"].astype(float)
    mediana = float(cdf["amount"].astype(float).median())
    p25_u, p75_u = np.percentile(montos_u, 25), np.percentile(montos_u, 75)
    if mediana < p25_u or mediana > p75_u:
        rasgos.append({
            "rasgo": "monto", "valor": "bajo" if mediana < p25_u else "alto", "casos": n,
            "mediana_grupo": round(mediana, 2),
            "rango_p25_p75_universo": [round(float(p25_u), 2), round(float(p75_u), 2)],
        })

    return sorted(rasgos, key=lambda r: -r.get("lift", 0))


def _fmt_rasgos(rasgos: list) -> str:
    if not rasgos:
        return "ninguno (el grupo se distingue por modalidad y contenido del caso, no por comercio, ciudad, tarjeta u horario)"
    partes = []
    for r in rasgos:
        if r["rasgo"] == "monto":
            partes.append(f"montos {r['valor']}s (mediana S/ {r['mediana_grupo']:,.2f})")
        else:
            partes.append(f"{r['rasgo']} {r['valor']} ({r['casos']} casos, {r['pct_grupo']}% vs {r['pct_universo']}% general)")
    return "; ".join(partes)


def generar_analisis_pre_llm(df: pd.DataFrame, base: dict, entidades: list) -> list:
    """
    Calcula deterministamente los perfiles de cluster y métricas P1, P2 y P3.
    """
    clusters_info = []

    for c_id in sorted(df["cluster"].unique()):
        cdf = df[df["cluster"] == c_id]
        total_casos = len(cdf)
        if total_casos == 0:
            continue

        montos = cdf["amount"].astype(float)
        impacto_total = float(montos.sum())
        monto_prom = float(montos.mean())
        monto_min = float(montos.min())
        monto_max = float(montos.max())

        # P2: Percentiles y Rangos Operativos
        p25 = float(np.percentile(montos, 25))
        p50 = float(np.percentile(montos, 50))
        p75 = float(np.percentile(montos, 75))

        # Comercios dominantes
        top_merchants = cdf["merchant_category"].value_counts().head(3).to_dict()
        top_canales = cdf["call_canal"].value_counts().head(3).to_dict()
        top_ciudades = cdf["customer_city"].value_counts().head(3).to_dict()
        top_tarjetas = cdf["card_type"].value_counts().head(2).to_dict()
        top_modalidades = cdf["modalidad_predicha"].value_counts().to_dict()

        # Confianza A1: % de casos con confianza >= 80 y promedio
        tasa_sospecha = round(100.0 * (cdf["conf_a1"] >= 80).sum() / total_casos, 1)
        confianza_a1_promedio = round(float(cdf["conf_a1"].mean()), 1)

        # Casos que A1 marcó como variante o modalidad nueva (patrones emergentes)
        emergentes = cdf[cdf["ai_estado_tipificacion"].isin(["Variante", "Nueva"])]

        # P1: Activación Parcial y Concentración
        modalidad_dominante = list(top_modalidades.keys())[0]
        pct_dominante = round(100.0 * top_modalidades[modalidad_dominante] / total_casos, 1)
        p1_resumen = f"Modalidad principal '{modalidad_dominante}' concentra el {pct_dominante}% de los casos del cluster."

        # P2: Resumen de distribución
        dispersion = "homogénea" if (p75 - p25) < (monto_prom * 0.4) else "dispersa"
        p2_resumen = f"Rango P25-P75: S/ {p25:.2f} a S/ {p75:.2f} (Mediana: S/ {p50:.2f}). Distribución {dispersion}."

        # P3: Inconsistencias o variantes
        firmas_mixtas = len(top_modalidades) > 1
        if firmas_mixtas:
            p3_resumen = f"Cluster heterogéneo con {len(top_modalidades)} modalidades. Posible vector compartido o tipología híbrida."
        else:
            p3_resumen = f"Cluster puro: 100% de los casos tipificados unívocamente como {modalidad_dominante}."

        clusters_info.append({
            "cluster_id": int(c_id),
            "total_casos": total_casos,
            "impacto_total": impacto_total,
            "monto_promedio": monto_prom,
            "monto_min": monto_min,
            "monto_max": monto_max,
            "tasa_sospecha": tasa_sospecha,
            "confianza_a1_promedio": confianza_a1_promedio,
            "modalidad_dominante": modalidad_dominante,
            "modalidades": top_modalidades,
            "casos_emergentes": {
                "total": len(emergentes),
                "por_estado": emergentes["ai_estado_tipificacion"].value_counts().to_dict(),
                "call_ids": emergentes["call_id"].tolist(),
            },
            "comercios_dominantes": top_merchants,
            "canales_de_reclamo": top_canales,
            "ciudades_dominantes": top_ciudades,
            "tarjetas_dominantes": top_tarjetas,
            "rasgos_distintivos": rasgos_distintivos(cdf, df),
            "anomalias": resumen_anomalias(cdf, base),
            "entidades_de_riesgo": [
                {"entidad_id": e["entidad_id"], "tipo": e["tipo"],
                 "casos_en_cluster": len(set(e["casos"]) & set(cdf["call_id"]))}
                for e in entidades if set(e["casos"]) & set(cdf["call_id"])
            ],
            "P1_concentracion": p1_resumen,
            "P2_rangos_operativos": p2_resumen,
            "P3_inconsistencias": p3_resumen,
            "casos_ids": cdf["call_id"].tolist(),
        })

    return clusters_info


# ---------------------------------------------------------------------------
# Paso 5: Síntesis Forense LLM con Claude Sonnet
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_FORENSIC = """Eres el analista de inteligencia de fraude (Pattern Investigator - Claims AI v3.0).
Se te proporcionan clusters generados matemáticamente (vía TF-IDF semántico de A1 + PCA + K-Means)
junto con los análisis cuantitativos pre-calculados (P1: concentraciones, P2: percentiles P25/P50/P75, P3: firmas e inconsistencias).

Tu misión: Analizar cada cluster y formular entre 3 a 5 PATRONES MAESTROS SISTÉMICOS que representen
ataques organizados o vulnerabilidades estructurales en el banco.

Reglas de interpretación:
- "canales_de_reclamo" es el medio por el que el cliente REPORTÓ el caso (contact center, app, agencia, etc.),
  NO el canal de la transacción. No lo presentes como vector de ataque ni como vulnerabilidad.
- "casos_emergentes" son casos que A1 marcó como Variante o Nueva: priorízalos si sugieren una tipología nueva.
- "rasgos_distintivos" son los únicos comercios, ciudades, tipos de tarjeta, franjas horarias o rangos de monto
  que están sobrerrepresentados en el cluster frente al total. Solo puedes afirmar una concentración (geográfica,
  de comercio, de horario, de monto) si figura ahí. Si la lista está vacía, NO atribuyas el patrón a una ciudad
  o comercio: descríbelo por su modalidad y por lo que muestran los hallazgos de A1.
- Un patrón debe cubrir 1 cluster, o 2 si son claramente la misma tipología. No formules patrones globales:
  los que abarquen más de la mitad de los casos se descartan automáticamente.
- "anomalias" compara cada cluster con las compras legítimas: % de casos con monto atípico, ciudad nueva,
  canal nuevo o lectura por banda/fallback frente al % de compras legítimas con esa señal. Úsalas para
  describir el vector de ataque.
- "entidades_de_riesgo" (en el reporte global) son terminales o comercios que concentran casos muy por encima
  de lo esperable por azar: "punto_de_compromiso" (las víctimas compraron ahí días antes del fraude: posible
  skimming o filtración) o "destino_comun" (ahí se cobraron varios fraudes: posible pasarela o comercio de
  cobro). Una entidad puede agrupar casos de varias modalidades: eso es un hallazgo relevante. Para un patrón
  basado en una entidad, indica "entidad_id" en lugar de "cluster_ids".
- Prioriza las entidades de riesgo, los clusters con más casos y los que tengan rasgos distintivos,
  anomalías marcadas o casos emergentes.
- NO escribas cifras (casos, montos, porcentajes): el sistema recalcula todas las métricas en código
  a partir de los clusters que indiques. Concéntrate en la interpretación.
- En "regla_propuesta" usa solo variables disponibles en los datos: monto, comercio/categoría, ciudad del
  cliente, ciudad del comercio, canal (POS/INT/APP), modo de entrada (CHIP/CONTACTLESS/BANDA/FALLBACK/
  NO_PRESENTE/APP), terminal_id, tipo de tarjeta, franja horaria, límite de crédito e historial de la tarjeta
  (ciudades, canales y montos previos).

IMPORTANTE: Sé conciso, directo y técnico en cada campo (1-2 frases por campo) para asegurar que el JSON sea completamente válido y no contenga saltos de línea sin escapar dentro de los strings:
- "titulo": Título técnico claro (máx. 120 caracteres).
- "modalidad_asociada": Modalidad principal del manual involucrada (clave exacta).
- "cluster_ids": Lista de IDs enteros de los clusters que respaldan el patrón (o [] si usas "entidad_id").
- "entidad_id": ID de la entidad de riesgo en la que se basa el patrón, o null.
- "ai_razonamiento_oculto": 2 frases con el vector de ataque y modus operandi.
- "descripcion": 1 a 2 frases explicando el patrón.
- "hipotesis": Hipótesis de fraude verificable por un analista, redactada como afirmación (1 frase).
- "analisis_reglas": Brecha de monitoreo (P1).
- "analisis_tipificaciones": Firmas y coherencia (P3).
- "accion_recomendada": Acción operativa inmediata.
- "regla_propuesta": Regla bancaria en pseudo-código (IF ... THEN ...).

Responde ÚNICAMENTE con el array JSON [...] válido, sin texto adicional ni antes ni después."""


def sintetizar_patrones_llm(client: anthropic.Anthropic, clusters_info: list, entidades: list, base: dict,
                            memoria_evaluada: list = None) -> list:
    contenido = (
        "Reporte Matemático de Clusters (TF-IDF + PCA + K-Means) y Métricas P1/P2/P3:\n\n"
        f"{json.dumps(clusters_info, ensure_ascii=False, indent=2)}\n\n"
        "Entidades de riesgo detectadas (terminales o comercios que concentran casos):\n\n"
        f"{json.dumps(entidades, ensure_ascii=False, indent=2)}\n\n"
        "Línea base: % de compras legítimas que presentan cada señal de anomalía:\n"
        f"{json.dumps(base, ensure_ascii=False)}"
    )
    if memoria_evaluada:
        contenido += (
            "\n\nPattern Memory: patrones ya evaluados por el analista. No repropongas los Descartados "
            "salvo que el cluster o la entidad traiga casos nuevos; si repites uno Validado o En Investigación, "
            "mantén su enfoque:\n"
            f"{json.dumps(memoria_evaluada, ensure_ascii=False, indent=2)}"
        )

    message = client.messages.create(
        model=MODELO_CLAUDE,
        max_tokens=6000,
        system=SYSTEM_PROMPT_FORENSIC,
        messages=[{"role": "user", "content": contenido}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()

    # Intento 1: Parseo directo con strict=False (tolera saltos de línea internos)
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        # Intento 2: Extraer el slice [ ... ]
        start_idx = raw.find("[")
        end_idx = raw.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            try:
                return json.loads(raw[start_idx : end_idx + 1], strict=False)
            except Exception as e2:
                print(f"Aviso de parseo: {e2}")

        # Intento 3: Reparar JSON cortado por fin de tokens
        if start_idx != -1:
            fragment = raw[start_idx:].strip()
            # Si termina en medio de un objeto, intentar cerrar comillas y llaves
            for ending in ['"}\n]', '}\n]', '\n]']:
                try:
                    return json.loads(fragment + ending, strict=False)
                except Exception:
                    continue

        print(f"Aviso: No se pudo parsear el JSON de patrones del LLM ({e}).")
        print("Muestra de respuesta recibida:")
        print(raw[:400] + " ... " + raw[-200:] if len(raw) > 600 else raw)
        return []


# ---------------------------------------------------------------------------
# Paso 6: Consolidación determinista (las cifras salen de los datos, no del LLM)
# ---------------------------------------------------------------------------
UMBRAL_IMPACTO_ALTO = 15000.0


UMBRAL_IMPACTO_MEDIO = 5000.0


PESOS_CONFIANZA = {"confianza_a1": 0.5, "pureza_modalidad": 0.3, "soporte": 0.2}


CASOS_SOPORTE_PLENO = 10  # con 10+ casos el componente de soporte vale 100


def nivel_impacto(monto: float) -> str:
    if monto > UMBRAL_IMPACTO_ALTO:
        return "Alto"
    if monto > UMBRAL_IMPACTO_MEDIO:
        return "Medio"
    return "Bajo"


def _top(serie: pd.Series, n: int = 3) -> dict:
    return {str(k): int(v) for k, v in serie.value_counts().head(n).items()}


def _fmt_top(d: dict) -> str:
    return ", ".join(f"{k} ({v})" for k, v in d.items()) or "N/A"


def _fmt_anomalias(anomalias: dict) -> str:
    if not anomalias:
        return "ninguna"
    return "; ".join(
        f"{s.replace('_', ' ')} {v['pct_casos']}% de los casos vs {v['pct_legitimas']}% en compras legítimas"
        for s, v in anomalias.items()
    )


def metricas_deterministas(cdf: pd.DataFrame, universo: pd.DataFrame, base: dict) -> dict:
    """Métricas, evidencia y confianza de un conjunto de casos, calculadas sin LLM."""
    n = len(cdf)
    montos = cdf["amount"].astype(float)
    p25, p50, p75 = (float(np.percentile(montos, q)) for q in (25, 50, 75))
    impacto = round(float(montos.sum()), 2)

    modalidades = _top(cdf["modalidad_predicha"], n=10)
    modalidad_dominante = next(iter(modalidades))
    pureza = 100.0 * modalidades[modalidad_dominante] / n
    conf_a1 = float(cdf["conf_a1"].mean())
    soporte = 100.0 * min(1.0, n / CASOS_SOPORTE_PLENO)
    confianza = (
        PESOS_CONFIANZA["confianza_a1"] * conf_a1
        + PESOS_CONFIANZA["pureza_modalidad"] * pureza
        + PESOS_CONFIANZA["soporte"] * soporte
    )

    evidencia = {
        "comercios": _top(cdf["merchant_category"]),
        "ciudades": _top(cdf["customer_city"]),
        "tipos_tarjeta": _top(cdf["card_type"], n=2),
        "canales_de_reclamo": _top(cdf["call_canal"]),
        "franjas_horarias": _top(cdf["franja_horaria"]),
        "modalidades": modalidades,
        "canales_transaccion": _top(cdf["canal_txn"]),
        "modos_entrada": _top(cdf["modo_entrada"]),
        "rasgos_distintivos": rasgos_distintivos(cdf, universo),
        "anomalias": resumen_anomalias(cdf, base),
    }
    dispersion = "homogénea" if (p75 - p25) < (montos.mean() * 0.4) else "dispersa"
    call_ids = [int(x) for x in cdf["call_id"].tolist()]

    evidencia_texto = (
        f"{n} casos (call_id: {', '.join(map(str, call_ids))}). "
        f"Impacto total S/ {impacto:,.2f}; monto promedio S/ {montos.mean():,.2f}; "
        f"rango S/ {montos.min():,.2f} - S/ {montos.max():,.2f}. "
        f"Comercios: {_fmt_top(evidencia['comercios'])}. "
        f"Ciudades: {_fmt_top(evidencia['ciudades'])}. "
        f"Tipo de tarjeta: {_fmt_top(evidencia['tipos_tarjeta'])}. "
        f"Franja horaria: {_fmt_top(evidencia['franjas_horarias'])}. "
        f"Modalidades A1: {_fmt_top(modalidades)}. "
        f"Canal de reclamo: {_fmt_top(evidencia['canales_de_reclamo'])}. "
        f"Canal de la transacción: {_fmt_top(evidencia['canales_transaccion'])}. "
        f"Modo de entrada: {_fmt_top(evidencia['modos_entrada'])}. "
        f"Rasgos distintivos frente al total: {_fmt_rasgos(evidencia['rasgos_distintivos'])}. "
        f"Anomalías frente al historial de cada tarjeta: {_fmt_anomalias(evidencia['anomalias'])}."
    )

    return {
        "num_casos": n,
        "impacto_total": impacto,
        "nivel_impacto": nivel_impacto(impacto),
        "tasa_sospecha": round(100.0 * (cdf["conf_a1"] >= 80).sum() / n, 2),
        "distribuciones": (
            f"Rango P25-P75: S/ {p25:.2f} a S/ {p75:.2f} (Mediana: S/ {p50:.2f}). Distribución {dispersion}."
        ),
        "modalidad_dominante": modalidad_dominante,
        "confianza": round(confianza, 2),
        "confianza_detalle": {
            "confianza_a1_promedio": round(conf_a1, 2),
            "pureza_modalidad": round(pureza, 2),
            "soporte": round(soporte, 2),
            "pesos": PESOS_CONFIANZA,
            "formula": "0.5*confianza_a1 + 0.3*pureza_modalidad + 0.2*soporte(min(1, n/10)*100)",
        },
        "casos_asociados": call_ids,
        "evidencia": evidencia,
        "evidencia_texto": evidencia_texto,
    }


# Confianza de patrones anclados a un comercio sospechoso: ahí la mezcla de tipos de fraude no
# indica un grupo difuso, es justamente el hallazgo. Su consistencia es el mayor valor entre la
# pureza de modalidad y la fuerza estadística de la concentración, que escala el p-valor corregido:
# p = 0,01 (umbral de detección) vale 50 y p <= 0,000001 vale 100.
P_VALOR_UMBRAL, P_VALOR_PLENO = 1e-2, 1e-6


def fuerza_estadistica(p_valor) -> float:
    import math
    log_p = -math.log10(max(float(p_valor), 1e-300))
    escala = (log_p - (-math.log10(P_VALOR_UMBRAL))) / (math.log10(P_VALOR_UMBRAL) - math.log10(P_VALOR_PLENO))
    return round(min(100.0, max(50.0, 50.0 + 50.0 * escala)), 2)


def confianza_con_entidad(detalle: dict, entidad: dict) -> tuple:
    """Recalcula la confianza de un patrón de comercio sospechoso. Devuelve (confianza, detalle)."""
    fuerza = fuerza_estadistica(entidad.get("p_valor_corregido", P_VALOR_UMBRAL))
    consistencia = max(float(detalle["pureza_modalidad"]), fuerza)
    confianza = (
        PESOS_CONFIANZA["confianza_a1"] * float(detalle["confianza_a1_promedio"])
        + PESOS_CONFIANZA["pureza_modalidad"] * consistencia
        + PESOS_CONFIANZA["soporte"] * float(detalle["soporte"])
    )
    return round(confianza, 2), {
        **detalle,
        "fuerza_estadistica": fuerza,
        "consistencia": round(consistencia, 2),
        "formula": "0.5*confianza_a1 + 0.3*max(pureza_modalidad, fuerza_estadistica) + 0.2*soporte "
                   "(patrón de comercio sospechoso)",
    }


# Prioridad: qué revisar primero, separada de la confianza (qué tan sólido es el hallazgo).
UMBRAL_CASOS_PRIORIDAD = 15
UMBRAL_CONFIANZA_PRIORIDAD = 85.0


def calcular_prioridad(impacto: float, num_casos: int, confianza: float, entidad) -> tuple:
    """Prioridad Alta / Media / Baja por puntos explicables. Devuelve (prioridad, detalle)."""
    puntos = {
        "impacto": 2 if impacto > UMBRAL_IMPACTO_ALTO else (1 if impacto > UMBRAL_IMPACTO_MEDIO else 0),
        "volumen": 1 if num_casos >= UMBRAL_CASOS_PRIORIDAD else 0,
        "confianza": 1 if confianza >= UMBRAL_CONFIANZA_PRIORIDAD else 0,
        "comercio_sospechoso": 1 if entidad else 0,
    }
    total = sum(puntos.values())
    prioridad = "Alta" if total >= 4 else ("Media" if total >= 2 else "Baja")
    return prioridad, {
        "puntos": puntos, "total": total,
        "regla": "impacto (alto 2, medio 1) + 15 o más reclamos (1) + confianza >= 85 (1) "
                 "+ comercio sospechoso (1); Alta >= 4, Media >= 2",
    }


# Un patrón que abarca más de esta fracción de los casos no discrimina ningún comportamiento
MAX_COBERTURA = 0.5


def consolidar_patrones(patrones_llm: list, df: pd.DataFrame, base: dict, entidades: list) -> list:
    """
    Combina la redacción del LLM con métricas recalculadas sobre los casos reales de los
    clusters que el LLM indicó. Descarta patrones que no referencian ningún cluster válido
    o que abarcan más de MAX_COBERTURA de los casos.
    """
    clusters_validos = set(int(c) for c in df["cluster"].unique())
    entidades_por_id = {e["entidad_id"]: e for e in entidades}
    consolidados = []

    for p in patrones_llm:
        entidad = entidades_por_id.get(str(p.get("entidad_id") or ""))
        if entidad:
            # Patrón anclado a una entidad: sus casos son los de la entidad, no los de clusters completos
            cdf = df[df["call_id"].isin(entidad["casos"])]
            ids = sorted({int(c) for c in cdf["cluster"]})
        else:
            cdf = None
        if cdf is None:
            ids = p.get("cluster_ids")
            if ids is None and p.get("cluster_id") is not None:
                ids = [p.get("cluster_id")]
            try:
                ids = sorted({int(c) for c in (ids or [])} & clusters_validos)
            except (TypeError, ValueError):
                ids = []
            if not ids:
                print(f"  Aviso: patrón '{p.get('titulo')}' sin clusters ni entidad válidos, se omite.")
                continue
            cdf = df[df["cluster"].isin(ids)]
        if len(cdf) > MAX_COBERTURA * len(df):
            print(f"  Aviso: patrón '{p.get('titulo')}' abarca {len(cdf)} de {len(df)} casos "
                  f"(más del {MAX_COBERTURA:.0%}); no discrimina, se omite.")
            continue
        m = metricas_deterministas(cdf, df, base)
        if entidad:
            m["evidencia"]["entidad_de_riesgo"] = entidad
            m["evidencia_texto"] = (
                f"Entidad de riesgo {entidad['entidad_id']} ({entidad['tipo'].replace('_', ' ')}): "
                f"{entidad['n_casos']} casos frente a {entidad['esperado_por_azar']} esperados por azar "
                f"(lift {entidad['lift']}x); {entidad['tarjetas_que_usaron_el_terminal']} tarjetas usaron "
                f"ese terminal en compras legítimas. " + m["evidencia_texto"]
            )

        if entidad:
            m["confianza"], m["confianza_detalle"] = confianza_con_entidad(m["confianza_detalle"], entidad)
        m["prioridad"], m["prioridad_detalle"] = calcular_prioridad(
            m["impacto_total"], m["num_casos"], m["confianza"], entidad)

        modalidad = str(p.get("modalidad_asociada") or "").strip()
        if modalidad not in m["evidencia"]["modalidades"]:
            modalidad = m["modalidad_dominante"]

        consolidados.append({
            "llm": p,
            "cluster_ids": ids,
            "titulo": str(p.get("titulo") or "Patrón sin título")[:150],
            "modalidad_asociada": modalidad[:50],
            "enunciado_hipotesis": p.get("hipotesis") or p.get("descripcion"),
            "entidad": entidad,
            **m,
        })

    return consolidados


# ---------------------------------------------------------------------------
# Paso 7: Relaciones entre entidades (co-ocurrencias en los casos de A1)
# ---------------------------------------------------------------------------
PARES_RELACION = [
    ("modalidad", "modalidad_predicha", "comercio", "merchant_category"),
    ("modalidad", "modalidad_predicha", "ciudad", "customer_city"),
    ("modalidad", "modalidad_predicha", "tipo_tarjeta", "card_type"),
    ("modalidad", "modalidad_predicha", "canal_txn", "canal_txn"),
    ("modalidad", "modalidad_predicha", "modo_entrada", "modo_entrada"),
    ("modalidad", "modalidad_predicha", "canal_reclamo", "call_canal"),
]


def construir_relaciones(df: pd.DataFrame, entidades: list) -> list:
    relaciones = []
    # Entidad de riesgo -> modalidades de los casos que concentra
    for e in entidades:
        sub = df[df["call_id"].isin(e["casos"])]
        for modalidad, g in sub.groupby("modalidad_predicha"):
            relaciones.append({
                "origen_tipo": "terminal",
                "origen": e["entidad_id"],
                "destino_tipo": "modalidad",
                "destino": str(modalidad)[:150],
                "peso": len(g),
                "casos": [int(x) for x in g["call_id"]],
                "clusters": sorted({int(x) for x in g["cluster"]}),
            })
    for tipo_o, col_o, tipo_d, col_d in PARES_RELACION:
        sub = df[[col_o, col_d, "call_id", "cluster"]].fillna("Desconocido")
        for (origen, destino), g in sub.groupby([col_o, col_d]):
            relaciones.append({
                "origen_tipo": tipo_o,
                "origen": str(origen)[:150],
                "destino_tipo": tipo_d,
                "destino": str(destino)[:150],
                "peso": len(g),
                "casos": [int(x) for x in g["call_id"]],
                "clusters": sorted({int(x) for x in g["cluster"]}),
            })
    return relaciones


# ---------------------------------------------------------------------------
# Paso 9: Pattern Memory (determinista, sin LLM ni auto-entrenamiento)
# ---------------------------------------------------------------------------
# Un patrón coincide con uno de la memoria si:
#   - ambos se basan en la misma entidad de riesgo (terminal/comercio), o
#   - ambos son de clusters de la misma modalidad y comparten al menos el 60% de los casos
#     del más pequeño (coeficiente de solapamiento; tolera que lleguen casos nuevos).
SOLAPAMIENTO_MIN = 0.6
MIN_CASOS_PATRON = 5              # por debajo, el patrón se marca "Bajo umbral"
MIN_CASOS_NUEVOS_EVIDENCIA = 3    # casos no vistos antes para considerar que hay evidencia nueva
ESTADOS_HEREDABLES = {"Validado", "En Investigación"}


def aplicar_parametros(params: dict) -> None:
    """Toma los parámetros de negocio de la corrida (tabla parametros_negocio) en vez de los valores por defecto."""
    global MIN_CASOS_PATRON, MIN_CASOS_NUEVOS_EVIDENCIA, MIN_CASOS_ENTIDAD, UMBRAL_IMPACTO_ALTO, UMBRAL_IMPACTO_MEDIO
    MIN_CASOS_PATRON = params.get("min_casos_patron", MIN_CASOS_PATRON)
    MIN_CASOS_NUEVOS_EVIDENCIA = params.get("min_casos_nuevos_evidencia", MIN_CASOS_NUEVOS_EVIDENCIA)
    MIN_CASOS_ENTIDAD = params.get("min_casos_entidad", MIN_CASOS_ENTIDAD)
    UMBRAL_IMPACTO_ALTO = params.get("umbral_impacto_alto", UMBRAL_IMPACTO_ALTO)
    UMBRAL_IMPACTO_MEDIO = params.get("umbral_impacto_medio", UMBRAL_IMPACTO_MEDIO)


# Catálogo vigente = Manual de Modalidades (sin "no_identificado", que no es una modalidad)
CATALOGO = set(MODALIDADES_VALIDAS) - {"no_identificado"}
FRACCION_NUEVA_MIN = 0.5   # si la mitad o más de sus casos fueron marcados como nueva modalidad, el patrón es nuevo


def clasificar_contra_catalogo(modalidad: str, num_casos: int, fraccion_nueva: float) -> str:
    """
    Síntesis del patrón según la propuesta (lámina 18):
      - Bajo umbral: menos casos que el mínimo configurado; se registra y se reevalúa.
      - Nuevo: no coincide con ninguna modalidad del catálogo (o la mayoría de sus casos
        fueron marcados como nueva modalidad por el Agente 1 o el analista).
      - Conocido: coincide con una modalidad vigente del catálogo; se reporta su crecimiento.
    """
    if num_casos < MIN_CASOS_PATRON:
        return "Bajo umbral"
    if modalidad not in CATALOGO or fraccion_nueva >= FRACCION_NUEVA_MIN:
        return "Nuevo"
    return "Conocido"


def version_prompt() -> str:
    """Huella corta del prompt del A2: cambia si el prompt cambia (trazabilidad)."""
    import hashlib
    return hashlib.sha256(SYSTEM_PROMPT_FORENSIC.encode("utf-8")).hexdigest()[:10]


def firma_patron(p: dict) -> tuple:
    """(tipo, firma, entidad_id) del patrón consolidado."""
    if p.get("entidad"):
        entidad_id = p["entidad"]["entidad_id"]
        return "entidad", f"entidad:{entidad_id}", entidad_id
    return "cluster", f"cluster:{p['modalidad_asociada']}", None


def _solapamiento(a: set, b: set) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def buscar_en_memoria(p: dict, memoria: list, usados: set):
    """Entrada de memoria que corresponde al patrón (o None) y el solapamiento de casos."""
    tipo, _, entidad_id = firma_patron(p)
    casos = set(p["casos_asociados"])
    mejor, mejor_score = None, 0.0
    for m in memoria:
        if m["memoria_id"] in usados:
            continue
        if tipo == "entidad":
            if m["tipo"] == "entidad" and m["entidad_id"] == entidad_id:
                return m, _solapamiento(casos, set(m["casos_vistos"] or []))
        elif m["tipo"] == "cluster" and m["modalidad"] == p["modalidad_asociada"]:
            score = _solapamiento(casos, set(m["casos_vistos"] or []))
            if score >= SOLAPAMIENTO_MIN and score > mejor_score:
                mejor, mejor_score = m, score
    return mejor, mejor_score


def aplicar_memoria(patrones: list, memoria: list, estados_a1: dict = None):
    """
    Clasifica cada patrón contra el catálogo (Nuevo / Conocido / Bajo umbral, ver
    clasificar_contra_catalogo) y lo cruza con la memoria para decidir qué se propone al analista.
    estados_a1: call_id -> "Nueva" / "Existente" (lo que marcó el Agente 1 o el analista).
    Reglas de control:
      - Un patrón Descartado por el analista no se vuelve a proponer, salvo que traiga al menos
        MIN_CASOS_NUEVOS_EVIDENCIA casos no vistos antes.
      - Si el analista lo había Validado o puesto En Investigación y no hay evidencia nueva,
        la hipótesis hereda esa decisión (no se le pide revisar lo mismo dos veces).
    Devuelve (patrones_a_proponer, omitidos).
    """
    propuestos, omitidos, usados = [], [], set()
    for p in patrones:
        m, solapamiento = buscar_en_memoria(p, memoria, usados)
        tipo, firma, entidad_id = firma_patron(p)
        if m:
            usados.add(m["memoria_id"])
            casos_nuevos = sorted(set(p["casos_asociados"]) - set(m["casos_vistos"] or []))
        else:
            casos_nuevos = list(p["casos_asociados"])
        evidencia_nueva = m is None or len(casos_nuevos) >= MIN_CASOS_NUEVOS_EVIDENCIA

        if m and m["estado"] == "Descartado" and not evidencia_nueva:
            omitidos.append({
                "titulo": p["titulo"], "firma": firma, "memoria_id": m["memoria_id"],
                "casos_nuevos": len(casos_nuevos), "comentario_analista": m.get("comentario"),
                "motivo": "Descartado por el analista y sin evidencia nueva suficiente",
            })
            continue

        casos_p = p["casos_asociados"]
        fraccion_nueva = (sum((estados_a1 or {}).get(c) == "Nueva" for c in casos_p) / len(casos_p)) if casos_p else 0.0
        clasificacion = clasificar_contra_catalogo(p["modalidad_asociada"], p["num_casos"], fraccion_nueva)

        heredar = bool(m) and m["estado"] in ESTADOS_HEREDABLES and not evidencia_nueva
        if heredar:
            estado, comentario = m["estado"], (
                f"Decisión heredada de Pattern Memory (corrida {m['ultima_corrida']}, sin evidencia nueva). "
                f"Comentario original: {m.get('comentario') or '—'}")
        elif m and m["estado"] == "Descartado":
            estado, comentario = "Pendiente revisión", (
                f"Fue descartado antes y reaparece con {len(casos_nuevos)} casos nuevos. "
                f"Comentario anterior: {m.get('comentario') or '—'}")
        else:
            estado, comentario = "Pendiente revisión", None

        propuestos.append({
            **p,
            "memoria": {
                "memoria_id": m["memoria_id"] if m else None,
                "tipo": tipo, "firma": firma, "entidad_id": entidad_id,
                "clasificacion": clasificacion,
                "casos_nuevos": len(casos_nuevos),
                "solapamiento": round(solapamiento, 2),
                "estado_inicial": estado,
                "comentario_inicial": comentario,
                "decision_heredada": heredar,
                "casos_vistos": sorted(set(m["casos_vistos"] or []) | set(p["casos_asociados"])) if m
                                else list(p["casos_asociados"]),
            },
        })
    return propuestos, omitidos


def resumen_memoria_para_llm(memoria: list) -> list:
    """Patrones ya evaluados por el analista, para que el LLM no los reproponga sin evidencia."""
    return [
        {"titulo": m["titulo"], "firma": m["firma"], "estado": m["estado"],
         "casos_vistos": len(m["casos_vistos"] or []), "comentario_analista": m.get("comentario")}
        for m in memoria if m["estado"] != "Pendiente revisión"
    ]
