"""
Recalcula la confianza, la prioridad y la síntesis (Nuevo / Conocido / Bajo umbral, comparando
con el catálogo de modalidades) de las hipótesis vigentes con las reglas y parámetros actuales del
Agente 2, a partir de lo que ya está guardado (no vuelve a ejecutar el agente ni llama a la IA).

Sirve para aplicar un cambio en la fórmula a la corrida vigente. Es idempotente.

Uso:
    python scripts/recalcular_confianza_prioridad.py
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)  # permite importar claims_ai desde scripts/ o db/

from claims_ai.models import database, parametros_model  # noqa: E402
from claims_ai.services import pattern_investigator as pi  # noqa: E402


def main():
    conn = database.connect()
    pi.aplicar_parametros(parametros_model.cargar_parametros(conn))
    with conn.cursor() as cur:
        # Lo que marcó el Agente 1 o el analista en cada caso (nueva modalidad o existente)
        cur.execute("""
            SELECT DISTINCT ON (call_id) call_id,
                   CASE WHEN estado_revision = 'Nueva modalidad' THEN 'Nueva'
                        ELSE COALESCE(ai_estado_tipificacion, 'Existente') END AS estado
            FROM tipificaciones ORDER BY call_id, tipificacion_id DESC;
        """)
        estados = {r["call_id"]: r["estado"] for r in cur.fetchall()}

        cur.execute("""
            SELECT h.hipotesis_id, h.memoria_id, h.confianza, h.confianza_detalle, h.evidencia,
                   h.impacto_monto, h.num_casos, h.clasificacion, p.titulo, p.modalidad_asociada,
                   p.casos_asociados
            FROM hipotesis h
            JOIN patrones p ON p.patron_id = h.patron_id
            WHERE h.vigente
            ORDER BY h.patron_id;
        """)
        filas = cur.fetchall()

        for h in filas:
            detalle = dict(h["confianza_detalle"])
            entidad = (h["evidencia"] or {}).get("entidad_de_riesgo")
            confianza = float(h["confianza"])
            if entidad:
                confianza, detalle = pi.confianza_con_entidad(detalle, entidad)
            prioridad, prioridad_detalle = pi.calcular_prioridad(
                float(h["impacto_monto"]), h["num_casos"], confianza, entidad)
            casos = h["casos_asociados"] or []
            fraccion_nueva = sum(estados.get(c) == "Nueva" for c in casos) / len(casos) if casos else 0.0
            clasificacion = pi.clasificar_contra_catalogo(h["modalidad_asociada"], h["num_casos"], fraccion_nueva)

            cur.execute(
                """
                UPDATE hipotesis
                SET confianza = %s, confianza_detalle = %s, prioridad = %s, prioridad_detalle = %s,
                    clasificacion = %s
                WHERE hipotesis_id = %s;
                """,
                (confianza, json.dumps(detalle), prioridad, json.dumps(prioridad_detalle), clasificacion,
                 h["hipotesis_id"]),
            )
            if h["memoria_id"]:
                cur.execute("UPDATE memoria_patrones SET confianza = %s WHERE memoria_id = %s;",
                            (confianza, h["memoria_id"]))
            print(f"  {h['titulo'][:60]:<60} confianza {float(h['confianza']):5.1f} -> {confianza:5.1f}"
                  f" | prioridad {prioridad} | {h['clasificacion']} -> {clasificacion}")
    conn.commit()
    conn.close()
    print(f"{len(filas)} hipótesis vigentes actualizadas.")


if __name__ == "__main__":
    main()
