"""Manual de Modalidades de fraude (data/manual_modalidades.json)."""
import json
import os

from claims_ai.config import MANUAL_PATH


def cargar_manual_modalidades() -> dict:
    if os.path.exists(MANUAL_PATH):
        with open(MANUAL_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {"modalidades": {}}


MANUAL_DATA = cargar_manual_modalidades()


VERSION_MANUAL = MANUAL_DATA.get("version", "—")

MODALIDADES_VALIDAS = list(MANUAL_DATA.get("modalidades", {}).keys())


def _texto_modalidad(clave: str, m: dict) -> str:
    """Una modalidad del Manual como la lee el Agente 1: definición, señales y cómo distinguirla."""
    lineas = [f"### {clave} — {m.get('nombre', clave)}", f"Definición: {m.get('definicion', '')}"]
    if m.get("riesgo_base"):
        lineas.append(f"Riesgo base: {m['riesgo_base']}")
    if m.get("indicadores_transcripcion"):
        lineas.append("Indicadores en la llamada: " + "; ".join(m["indicadores_transcripcion"]) + ".")
    if m.get("frases_cliente"):
        lineas.append("Frases típicas del cliente: " + "; ".join(f"«{f}»" for f in m["frases_cliente"]) + ".")
    trans = m.get("indicadores_transaccionales") or {}
    partes = []
    if trans.get("canales"):
        partes.append("canales " + ", ".join(trans["canales"]))
    if trans.get("entry_modes"):
        partes.append("modos de entrada " + ", ".join(trans["entry_modes"]))
    if trans.get("patron_frecuente"):
        partes.append(trans["patron_frecuente"])
    if partes:
        lineas.append("Indicadores en la transacción: " + "; ".join(partes) + ".")
    for otra, criterio in (m.get("diferenciar_de") or {}).items():
        lineas.append(f"Diferenciar de {otra}: {criterio}")
    return "\n".join(lineas)


# Manual completo para el prompt del Agente 1 (todas las secciones, no solo la definición)
MODALIDADES_TEXTO = "\n\n".join(
    _texto_modalidad(k, v) for k, v in MANUAL_DATA.get("modalidades", {}).items()
)

# Casos que no son fraude (ficha 2) y reglas generales de tipificación
NO_FRAUDE = MANUAL_DATA.get("no_fraude", [])
NO_FRAUDE_TEXTO = "\n".join(
    f"- {n['id']} {n['nombre']}: señales {'; '.join(n.get('senales', []))}. Acción: {n.get('accion', '')}"
    for n in NO_FRAUDE
)
REGLAS_TIPIFICACION_TEXTO = "\n".join(f"- {r}" for r in MANUAL_DATA.get("reglas_tipificacion", []))
