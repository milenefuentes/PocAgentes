"""Vista de consola del Agente 1: progreso por caso, fallas y métricas."""


def mostrar_mensaje(texto: str):
    print(texto)


def mostrar_resultado_caso(idx: int, total: int, caso: dict, resultado: dict):
    call_id = caso["call_id"]
    mod_pred = str(resultado.get("ai_tipificacion") or "no_identificado")
    mod_real = caso.get("modalidad_real")
    acierto = "[OK]" if (mod_real and mod_real in mod_pred) else "[X ]"
    print(f"[{idx}/{total}] {acierto} call_id={call_id:<4} | Real: {str(mod_real):<22} "
          f"| Predicho: {mod_pred:<22} | Blindaje: {resultado.get('ai_blindaje', '')[:40]}...")


def mostrar_fallidos(fallidos: list):
    if fallidos:
        print(f"\n{len(fallidos)} caso(s) fallaron y quedan pendientes para la próxima corrida:")
        for call_id, err in fallidos:
            print(f"  - call_id={call_id}: {type(err).__name__}: {err}")


def mostrar_metricas(metricas: dict):
    """Reporte de métricas v3.0 con acierto, distribución de modalidades y ejemplos de blindaje."""
    total = metricas["total"]
    correctas = metricas["correctas"]
    conf_row = metricas["confianza"]
    por_modalidad = metricas["por_modalidad"]
    ejemplos_blindaje = metricas["ejemplos_blindaje"]

    print("\n=======================================================")
    print("      MÉTRICAS DEL AGENTE 1 (CLAIMS AI v3.0)           ")
    print("=======================================================")
    print(f"Total de casos procesados : {total}")
    if total:
        pct = 100.0 * correctas / total
        print(f"Clasificaciones correctas : {correctas} ({pct:.1f}%)")
    if conf_row["n"]:
        print(f"Confianza promedio (LLM)  : {float(conf_row['conf']):.1f}/100 "
              f"({conf_row['n']} casos con confianza numérica)")

    print("\nDistribución por Modalidad Predicha:")
    for m in por_modalidad:
        print(f"  - {str(m['modalidad']):<28}: {m['n']} casos")

    if ejemplos_blindaje:
        print("\nEjemplos de Reglas de Blindaje Propuestas (ai_blindaje):")
        for b in ejemplos_blindaje:
            print(f"  [call_id {b['call_id']}] ({b['ai_tipificacion']})")
            print(f"    --> {b['ai_blindaje']}")
    print("=======================================================\n")
