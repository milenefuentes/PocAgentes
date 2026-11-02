"""
Controlador del Agente 2 – Pattern Investigator.
Orquesta el pipeline: carga los casos y transacciones (modelo), calcula anomalías,
entidades de riesgo, clusters y la síntesis con Claude (servicio), guarda la corrida
versionada (modelo) y muestra el progreso y el reporte maestro (vista).

Uso:
    python agente2_patrones.py             # Ejecuta el pipeline completo de clustering y síntesis
    python agente2_patrones.py --ver       # Visualiza los patrones vigentes y sus hipótesis
"""
import argparse

import anthropic

from claims_ai.config import MODELO_CLAUDE
from claims_ai.models import database, parametros_model, patron_model
from claims_ai.services import pattern_investigator as pi
from claims_ai.views import consola_agente2 as vista


def main():
    parser = argparse.ArgumentParser(description="Agente 2 Pattern Investigator (Claims AI v3.0)")
    parser.add_argument("--ver", action="store_true", help="Visualizar los patrones maestros ya registrados")
    args = parser.parse_args()

    conn = database.connect()
    vista.mostrar_mensaje("Conexión a PostgreSQL (superbank_db) verificada.")

    if args.ver:
        vista.mostrar_reporte_maestro(patron_model.obtener_patrones_vigentes(conn))
        conn.close()
        return

    vista.mostrar_mensaje("\nIniciando Pipeline de Inteligencia de Fraude (A2 v3.0)...")
    # Parámetros de negocio (configurables desde el portal): ventana, umbrales y exclusiones
    parametros = parametros_model.cargar_parametros(conn)
    pi.aplicar_parametros(parametros)
    ventana = parametros["ventana_dias"]
    vista.mostrar_mensaje(
        f"Parámetros: ventana {'de ' + str(ventana) + ' días' if ventana else 'completa'}, "
        f"mínimo {parametros['min_casos_patron']} casos por patrón, "
        f"excluidas: {', '.join(parametros['modalidades_excluidas']) or 'ninguna'}.")
    df = patron_model.cargar_universo_enriquecido(conn, ventana, parametros["modalidades_excluidas"])
    if df.empty:
        vista.mostrar_mensaje("No se encontraron casos tipificados. Ejecuta primero agente1_tipificador.py.")
        conn.close()
        return

    vista.mostrar_mensaje(f"Cargados {len(df)} casos desde el universo de tipificaciones de A1.")

    # Pattern Memory: patrones ya detectados y la última decisión del analista
    patron_model.adoptar_vigentes_en_memoria(conn, pi.MIN_CASOS_PATRON)
    memoria = patron_model.cargar_memoria(conn)
    vista.mostrar_mensaje(f"Pattern Memory: {len(memoria)} patrones registrados "
                          f"({sum(m['estado'] != 'Pendiente revisión' for m in memoria)} con decisión del analista).")

    # 0. Anomalías frente al historial de cada tarjeta y entidades de riesgo
    vista.mostrar_mensaje("Calculando anomalías frente al historial de cada tarjeta y entidades de riesgo...")
    tx = pi.marcar_anomalias(patron_model.cargar_transacciones(conn))
    base = pi.linea_base_legitima(tx)
    df = pi.enriquecer_con_anomalias(df, tx)
    entidades = pi.detectar_entidades_riesgo(df, tx)
    vista.mostrar_entidades(entidades)

    # 1 y 2. Feature Engineering + TF-IDF
    vista.mostrar_mensaje("Ejecutando Feature Engineering determinista y vectorización TF-IDF (100 términos)...")
    X, df_features = pi.pipeline_feature_engineering(df)
    df_features = pi.agregar_confianza_a1(df_features)

    # 3. PCA + K-Means Adaptativo
    vista.mostrar_mensaje("Optimizando K-Means adaptativo con evaluación de Silhouette Score...")
    df_clustered, best_k, best_score = pi.clustering_k_means_optimo(X, df_features)
    vista.mostrar_mensaje(f"K óptimo seleccionado: {best_k} clusters (Silhouette Score: {best_score:.3f}).")

    # 4. Análisis Pre-LLM P1/P2/P3
    vista.mostrar_mensaje("Calculando métricas Pre-LLM (P1: Brechas, P2: Rangos operativos, P3: Inconsistencias)...")
    clusters_info = pi.generar_analisis_pre_llm(df_clustered, base, entidades)

    # 5. Síntesis Forense LLM
    vista.mostrar_mensaje("Enviando perfiles matemáticos a Claude Sonnet para síntesis forense...")
    client = anthropic.Anthropic()
    patrones_llm = pi.sintetizar_patrones_llm(client, clusters_info, entidades, base,
                                              pi.resumen_memoria_para_llm(memoria))

    # 6. Métricas deterministas sobre los clusters que indicó el LLM
    patrones = pi.consolidar_patrones(patrones_llm, df_clustered, base, entidades)
    if not patrones:
        vista.mostrar_mensaje("El modelo no devolvió patrones estructurados con clusters válidos.")
        conn.close()
        return

    # 6b. Pattern Memory: nuevo / conocido / bajo umbral y filtro de descartados sin evidencia nueva
    estados_a1 = dict(zip(df_clustered["call_id"], df_clustered["ai_estado_tipificacion"]))
    patrones, omitidos = pi.aplicar_memoria(patrones, memoria, estados_a1)
    vista.mostrar_memoria(patrones, omitidos)
    if not patrones:
        vista.mostrar_mensaje("Todos los patrones detectados ya fueron descartados por el analista sin evidencia nueva.")
        conn.close()
        return

    # 7. Relaciones entre entidades
    relaciones = pi.construir_relaciones(df_clustered, entidades)

    # 8. Guardar (versionado) y mostrar
    vista.mostrar_mensaje(
        f"Guardando {len(patrones)} patrones/hipótesis y {len(relaciones)} relaciones en una corrida nueva...")
    corrida_id = patron_model.guardar_corrida(conn, df_clustered, best_k, best_score, patrones, relaciones,
                                              omitidos, MODELO_CLAUDE, pi.version_prompt(), parametros)
    vista.mostrar_mensaje(f"Corrida {corrida_id} registrada; los patrones anteriores quedan como no vigentes.")

    vista.mostrar_reporte_maestro(patron_model.obtener_patrones_vigentes(conn))
    conn.close()
