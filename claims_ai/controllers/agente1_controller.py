"""
Controlador del Agente 1 – Fraud Analyst.
Orquesta el flujo: toma las llamadas pendientes (modelo), analiza cada una con Claude
(servicio), guarda la ficha (modelo) y muestra el progreso y las métricas (vista).

Uso:
    python agente1_tipificador.py --limite 5          # Procesa 5 casos concurrentemente
    python agente1_tipificador.py --todas             # Procesa todas las pendientes
    python agente1_tipificador.py --evaluar           # Muestra métricas sin llamar al LLM
    python agente1_tipificador.py --reprocesar --estimar        # Costo estimado de reprocesar (sin llamar al LLM)
    python agente1_tipificador.py --reprocesar --ids 58 1       # Reprocesa solo esas llamadas
    python agente1_tipificador.py --reprocesar --todas          # Reprocesa todas las analizadas con otra versión
"""
import argparse
import asyncio

import anthropic

from claims_ai.config import MODELO_CLAUDE
from claims_ai.models import caso_model, database
from claims_ai.models.manual_model import VERSION_MANUAL
from claims_ai.services import fraud_analyst
from claims_ai.services.enmascarador import enmascarar
from claims_ai.views import consola_agente1 as vista


async def procesar_caso_concurrente(
    sem: asyncio.Semaphore,
    client: anthropic.AsyncAnthropic,
    conn,
    caso: dict,
    idx: int,
    total: int,
    corrida: dict,
):
    async with sem:
        call_id = caso["call_id"]
        tickets = caso_model.obtener_tickets_cliente(conn, caso.get("customer_id"), caso.get("fecha_llamada"))
        compras = caso_model.obtener_historial_tarjeta(conn, caso.get("card_id"), caso.get("card_txn_id"),
                                                       caso.get("txn_date"))

        # Fase 0: datos sensibles (DNI, tarjetas, correos, teléfonos, direcciones) se ocultan antes de la IA
        transcript, enmascarados = enmascarar(caso["transcript"])

        # Fase 1: Sanitización
        dialogo_limpio = await fraud_analyst.sanitizar_transcripcion(client, transcript)

        # Fase 2: Análisis forense ciego
        resultado = await fraud_analyst.analizar_caso_forense(client, caso, dialogo_limpio, tickets, compras)

        # Persistencia en BD, junto con el texto exacto que recibió Claude (trazabilidad)
        contexto = fraud_analyst.construir_contexto(caso, dialogo_limpio, tickets, compras)
        caso_model.guardar_tipificacion(conn, call_id, caso.get("modalidad_real"), resultado, contexto,
                                        {**corrida, "datos_enmascarados": enmascarados})

        vista.mostrar_resultado_caso(idx, total, caso, resultado)


async def async_main(args):
    conn = database.connect()
    vista.mostrar_mensaje("Conexión a PostgreSQL (superbank_db) verificada.")

    if args.evaluar:
        vista.mostrar_metricas(caso_model.obtener_metricas(conn))
        conn.close()
        return

    limite = None if (args.todas or args.ids) else args.limite
    version = fraud_analyst.version_prompt()
    pendientes = caso_model.obtener_casos_pendientes(conn, limite, version if args.reprocesar else None, args.ids)

    if args.estimar:
        vista.mostrar_mensaje(estimar_costo(conn, pendientes, args.reprocesar))
        conn.close()
        return

    if not pendientes:
        vista.mostrar_mensaje("No hay llamadas pendientes de tipificar.")
        vista.mostrar_metricas(caso_model.obtener_metricas(conn))
        conn.close()
        return

    total = len(pendientes)
    concurrencia = max(1, args.concurrencia)
    vista.mostrar_mensaje(f"Iniciando procesamiento de {total} casos con concurrencia máxima de {concurrencia}...\n")

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(concurrencia)

    # Cada ejecución queda registrada como una corrida: modelo, versión de las instrucciones y del Manual
    corrida = {"modelo": MODELO_CLAUDE, "prompt_version": fraud_analyst.version_prompt(),
               "version_manual": VERSION_MANUAL}
    corrida["corrida_a1_id"] = caso_model.iniciar_corrida_a1(conn, **corrida)
    vista.mostrar_mensaje(f"Corrida {corrida['corrida_a1_id']} del Agente 1 · Manual v{VERSION_MANUAL} · "
                          f"instrucciones {corrida['prompt_version']}")

    # El primer caso va solo: así escribe la caché de las instrucciones y los demás (en paralelo) la leen.
    # Si arrancaran todos juntos, ninguno podría leer lo que los otros aún están escribiendo.
    async def seguro(caso, i):
        try:
            return await procesar_caso_concurrente(sem, client, conn, caso, i + 1, total, corrida)
        except Exception as e:   # un caso que falla no aborta el resto del lote
            return e
    resultados = [await seguro(pendientes[0], 0)]
    resultados += await asyncio.gather(*[seguro(c, i) for i, c in enumerate(pendientes) if i > 0])
    fallidos = [
        (caso["call_id"], r) for caso, r in zip(pendientes, resultados) if isinstance(r, Exception)
    ]
    vista.mostrar_fallidos(fallidos)
    caso_model.cerrar_corrida_a1(conn, corrida["corrida_a1_id"], total, len(fallidos))
    u = fraud_analyst.USO
    vista.mostrar_mensaje(
        f"\nCosto real de la corrida: US$ {fraud_analyst.costo_real():.3f} ({u['llamadas']} llamadas a la IA; "
        f"entrada {u['entrada']:,} + caché escrita {u['cache_escritura']:,} + caché leída {u['cache_lectura']:,} "
        f"tokens; salida {u['salida']:,} tokens).")

    vista.mostrar_metricas(caso_model.obtener_metricas(conn))
    conn.close()


PRECIO_ENTRADA, PRECIO_SALIDA = fraud_analyst.PRECIO_ENTRADA, fraud_analyst.PRECIO_SALIDA
CARACTERES_POR_TOKEN = 2.5   # calibrado con el gasto real de la corrida 2 (texto en español + JSON)


def estimar_costo(conn, casos: list, reprocesar: bool) -> str:
    """
    Costo aproximado de analizar `casos` sin llamar al LLM: arma el mismo texto que se enviaría y
    usa como salida el largo promedio de las fichas ya generadas (+15 % por los campos nuevos).
    """
    if not casos:
        return "No hay casos que procesar con esos filtros."
    with conn.cursor() as cur:
        cur.execute("""
            SELECT AVG(LENGTH(raw_payload::text)) AS salida FROM tipificaciones
            WHERE ai_contexto_entrada IS NOT NULL;
        """)
        salida_prom = float(cur.fetchone()["salida"] or 4000) * 1.15
    tok = lambda texto: len(texto) / CARACTERES_POR_TOKEN
    entrada = salida = 0.0
    for caso in casos:
        tickets = caso_model.obtener_tickets_cliente(conn, caso.get("customer_id"), caso.get("fecha_llamada"))
        compras = caso_model.obtener_historial_tarjeta(conn, caso.get("card_id"), caso.get("card_txn_id"),
                                                       caso.get("txn_date"))
        transcript = caso["transcript"] or ""
        # Llamada 1 (limpieza del diálogo) + llamada 2 (análisis con todo el contexto)
        entrada += tok(fraud_analyst.SYSTEM_PROMPT_SANITIZER + transcript)
        salida += tok(transcript)
        entrada += tok(fraud_analyst.construir_contexto(caso, transcript, tickets, compras))
        salida += tok("x" * int(salida_prom))
    # Instrucciones con el Manual: se escriben en caché una vez (x1,25) y se leen en los demás casos (x0,1)
    instrucciones = tok(fraud_analyst.SYSTEM_PROMPT_ANALYST)
    entrada += instrucciones * 1.25 + instrucciones * 0.1 * (len(casos) - 1)
    costo = entrada / 1e6 * PRECIO_ENTRADA + salida / 1e6 * PRECIO_SALIDA
    return (f"{'Reprocesar' if reprocesar else 'Analizar'} {len(casos)} casos: ~{entrada:,.0f} tokens de entrada y "
            f"~{salida:,.0f} de salida. Costo estimado: US$ {costo:.2f} (US$ {costo / len(casos):.3f} por caso). "
            f"No se llamó a la IA.")


def main():
    parser = argparse.ArgumentParser(description="Agente 1 Fraud Analyst (Claims AI v3.0)")
    parser.add_argument("--todas", action="store_true", help="Procesar todas las llamadas pendientes")
    parser.add_argument("--limite", type=int, default=5, help="Límite de llamadas a procesar (default: 5)")
    parser.add_argument("--concurrencia", type=int, default=5, help="Nivel de concurrencia simultánea (default: 5)")
    parser.add_argument("--evaluar", action="store_true", help="Solo mostrar reporte de métricas y blindaje")
    parser.add_argument("--reprocesar", action="store_true",
                        help="Volver a analizar casos ya tipificados con otra versión de las instrucciones")
    parser.add_argument("--ids", type=int, nargs="+", help="Solo estas llamadas (call_id)")
    parser.add_argument("--estimar", action="store_true", help="Calcular el costo aproximado sin llamar a la IA")
    args = parser.parse_args()

    asyncio.run(async_main(args))
