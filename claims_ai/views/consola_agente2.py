"""Vista de consola del Agente 2: progreso del pipeline, entidades de riesgo y reporte maestro."""


def mostrar_mensaje(texto: str):
    print(texto)


def mostrar_entidades(entidades: list):
    for e in entidades:
        print(f"  Entidad de riesgo {e['entidad_id']} ({e['tipo']}): {e['n_casos']} casos vs "
              f"{e['esperado_por_azar']} esperados (lift {e['lift']}x) | modalidades A1: {e['modalidades_a1']}")


def mostrar_memoria(patrones: list, omitidos: list):
    """Clasificación de Pattern Memory de cada patrón propuesto y los omitidos."""
    for p in patrones:
        m = p["memoria"]
        extra = " · decisión heredada: " + m["estado_inicial"] if m["decision_heredada"] else ""
        print(f"  [{m['clasificacion']}] {p['titulo'][:70]} ({m['casos_nuevos']} casos nuevos){extra}")
    for o in omitidos:
        print(f"  [Omitido por memoria] {o['titulo'][:70]} — {o['motivo']} ({o['casos_nuevos']} casos nuevos)")


def mostrar_reporte_maestro(patrones: list):
    """Reporte maestro de los patrones vigentes y sus hipótesis."""
    if not patrones:
        print("\nNo hay patrones guardados. Ejecuta 'python agente2_patrones.py' primero.")
        return

    print("\n" + "=" * 78)
    print("      REPORTE MAESTRO DE INTELIGENCIA DE FRAUDE (CLAIMS AI v3.0)      ")
    print("=" * 78)
    corrida = patrones[0].get("corrida_id")
    print(f"Patrones vigentes: {len(patrones)} (corrida {corrida if corrida is not None else 'anterior a v3.1'})\n")

    for p in patrones:
        titulo = (p.get("titulo") or "Patrón sin título").upper()
        clusters = p.get("clusters_origen") or "N/A"
        impacto = float(p.get("impacto_total") or 0.0)
        num_casos = p.get("num_casos") or 0

        print(f"[{p['patron_id']}] {titulo}")
        print(f"    • Modalidad Principal : {p.get('modalidad_asociada', 'N/A')} (Clusters {clusters})")
        print(f"    • Casos Identificados : {num_casos} casos")
        if p.get("clasificacion"):
            heredada = " · decisión heredada" if p.get("decision_heredada") else ""
            print(f"    • Pattern Memory      : {p['clasificacion']} ({p.get('casos_nuevos') or 0} casos nuevos){heredada}")
        print(f"    • Impacto Económico   : S/ {impacto:,.2f} ({p.get('nivel_impacto') or 'N/A'})")
        if p.get("hipotesis"):
            conf = float(p.get("confianza_hipotesis") or 0.0)
            print(f"    • Hipótesis           : {p['hipotesis']}")
            print(f"    • Confianza / Estado  : {conf:.1f}% | {p.get('estado_hipotesis')}")
        print(f"    • Hallazgo Forense    : {p.get('descripcion', '')}")
        print(f"    • Evidencia Estadística: {p.get('evidencia', '')}")
        if p.get("distribuciones"):
            print(f"    • Distribución P25-P75: {p['distribuciones']}")
        if p.get("analisis_reglas"):
            print(f"    • Análisis de Brechas : {p['analisis_reglas']}")
        if p.get("accion_recomendada"):
            print(f"    • Acción Inmediata    : {p['accion_recomendada']}")
        print(f"    • REGLA PROPUESTA     : {p.get('regla_propuesta', '')}")
        print("-" * 78)
    print("=" * 78 + "\n")
