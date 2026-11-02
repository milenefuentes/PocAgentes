"""
Agente 1 – Fraud Analyst: lógica de análisis de cada caso con Claude.
Sanitiza la transcripción y la analiza junto con los datos transaccionales, del
cliente y sus tickets, devolviendo una ficha estructurada (JSON de 12 campos).
"""
import asyncio
import json
import re

import anthropic

from claims_ai.config import MODELO_CLAUDE
from claims_ai.models.manual_model import MODALIDADES_TEXTO, NO_FRAUDE_TEXTO, REGLAS_TIPIFICACION_TEXTO, VERSION_MANUAL


SYSTEM_PROMPT_ANALYST = f"""Eres un analista senior de prevención de fraude bancario (Fraud Analyst - Claims AI).
Tu labor es investigar transcripciones de llamadas y reclamos de clientes cruzándolos con datos técnicos de transacciones para:
1. Determinar objetivamente qué ocurrió y clasificar el caso según las modalidades de fraude bancario conocidas.
2. Extraer insights técnicos (vectores de compromiso, canales, indicadores e-commerce, terminales).
3. Proponer una acción operativa de mitigación y una pseudo-regla preliminar de blindaje (IF ... THEN ...).

Manual de Modalidades Operativas (v{VERSION_MANUAL}):

{MODALIDADES_TEXTO}

Casos que NO son fraude (catálogo del manual):
{NO_FRAUDE_TEXTO}

Reglas de tipificación del manual:
{REGLAS_TIPIFICACION_TEXTO}

Instrucciones estrictas:
- No tienes acceso a etiquetas previas ni confirmación de fraude; debes inferirlo de la evidencia fáctica.
- Si el patrón coincide con una modalidad del manual, clasifícala en `ai_tipificacion` usando esa clave exacta y pon `ai_estado_tipificacion`: "Existente".
- Si observas una variación nueva de la modalidad o un patrón emergente, usa `ai_estado_tipificacion`: "Variante" o "Nueva".
- Para `ai_blindaje`, formula una regla técnica preliminar usando sintaxis bancaria (ej: IF ACF_CANAL='INT' AND ACF_MONTO > 500 THEN REQUIRE_3DS).
  Usa solo variables que existan en los datos recibidos (monto, comercio/categoría, fecha/hora, canal, modo de entrada, ciudad del comercio, ciudad del cliente, tipo de tarjeta, límite); no inventes campos que no se te entregaron.
- El "canal de atención" es el medio por el que el cliente REPORTÓ el caso, no el canal por el que se hizo la transacción; no lo trates como vector de ataque.
- El historial de tickets del cliente es contexto adicional (reclamos previos, bloqueos, problemas con la app); úsalo solo si es pertinente.
- El historial de compras de la tarjeta (90 días previos) muestra cómo compra normalmente el cliente. Úsalo para
  juzgar si la transacción reclamada es inusual (ciudad, canal, modo de entrada, rubro o monto nunca vistos); no
  incluye ninguna etiqueta de fraude.
- Usa los criterios "Diferenciar de" del manual para elegir entre modalidades parecidas; tipifica por el ORIGEN
  del compromiso, no por cómo se usó después la tarjeta.
- Los datos personales vienen ocultos con etiquetas como [DNI], [TARJETA], [CORREO], [TELEFONO] o [DIRECCION]; no
  intentes deducirlos.
- `ai_situacion` clasifica el caso en una de tres situaciones:
  * "fraude": un tercero usó la tarjeta, la cuenta o las credenciales sin consentimiento del titular (incluye todas
    las modalidades del manual).
  * "no_fraude": el propio cliente hizo o autorizó el cargo, lo reconoce durante la llamada, o es un error comercial
    (cobro duplicado, devolución pendiente, monto distinto al acordado) sin intervención de terceros. En este caso
    usa `ai_tipificacion`: "no_identificado" y explica en `ai_motivo_situacion` por qué no califica como fraude,
    indicando cuál caso del catálogo de no fraude aplica (NF01 a NF05).
  * "ilegible": la transcripción no permite entender el reclamo (cortes, ruido, frases incoherentes). No adivines
    la modalidad: usa `ai_tipificacion`: "no_identificado", `ai_confianza` menor a 30 y describe en
    `ai_motivo_situacion` qué tramos no se entienden.
  Si la situación es "fraude", deja `ai_motivo_situacion` vacío.
- `ai_evidencia` son de 1 a 3 frases COPIADAS TEXTUALMENTE del diálogo (sin parafrasear) que sustentan tu
  conclusión, cada una con lo que demuestra. Si el caso es ilegible, cita los fragmentos que no se entienden.
- `ai_confianza` es un entero 0-100 que refleja qué tan seguro estás de la tipificación dada la evidencia: >=80 evidencia clara y coherente, 50-79 evidencia parcial o ambigua, <50 información insuficiente o contradictoria.
- Todos los campos `*_tags` deben contener etiquetas separadas por punto y coma (;) útiles para clustering semántico y grafos de contagio.

Responde SIEMPRE con un ÚNICO objeto JSON válido, sin bloques explicativos adicionales, con este esquema exacto:
{{
  "ai_razonamiento_oculto": "<Análisis técnico paso a paso de la discrepancia o vector de ataque>",
  "ai_dialogo_limpio": "<Resumen limpio y secuencial de la conversación entre Agente y Cliente>",
  "ai_resumen_caso": "Motivo: <motivo>. Monto: S/ <monto>. Tipo: <tipo>. Canal: <canal>. Experiencia: <detalle>",
  "ai_resumen_caso_tags": "<tag1; tag2; tag3>",
  "ai_situacion": "fraude|no_fraude|ilegible",
  "ai_motivo_situacion": "<vacío si es fraude; si no, por qué no califica o qué no se entiende>",
  "ai_evidencia": [{{"cita": "<frase textual del diálogo>", "sustenta": "<qué demuestra>"}}],
  "ai_tipificacion": "<clave_de_modalidad_o_nueva>",
  "ai_tipificacion_tags": "<tag1; tag2>",
  "ai_estado_tipificacion": "Existente|Variante|Nueva",
  "ai_confianza": <entero 0-100>,
  "ai_insights": "<Hallazgos sobre canales, vulnerabilidades o filtraciones de credenciales>",
  "ai_insights_tags": "<tag1; tag2>",
  "ai_accion_recomendada": "<Acción bancaria concreta de atención o mitigación>",
  "ai_accion_recomendada_tags": "<tag1; tag2>",
  "ai_blindaje": "IF <condición técnica> THEN <acción de regla>",
  "ai_blindaje_tags": "<tag1; tag2>"
}}
"""


SYSTEM_PROMPT_SANITIZER = """Eres un asistente especializado en procesamiento de texto bancario.
Tu tarea es leer una transcripción cruda de llamada de soporte o reclamo y devolver una versión
limpia y legible, eliminando muletillas, ruido de audio, cortes de línea innecesarios o caracteres
extraños, pero preservando fielmente todos los números, fechas, nombres de comercios, montos y hechos
descritos por el cliente.
Etiqueta cada intervención con su hablante (Agente: ... / Cliente: ...) solo si ese hablante aparece
en la transcripción. Si solo habla el cliente, usa únicamente "Cliente:". Nunca inventes frases,
preguntas ni respuestas que no estén en el texto original.
Devuelve únicamente el diálogo limpio, sin introducciones ni notas."""


# Tokens consumidos en la corrida (para mostrar el costo real al terminar)
USO = {"entrada": 0, "cache_escritura": 0, "cache_lectura": 0, "salida": 0, "llamadas": 0}
# Precio de lista de Claude Sonnet 4.6 (US$ por millón de tokens); la caché escribe a 1,25x y lee a 0,1x
PRECIO_ENTRADA, PRECIO_SALIDA = 3.0, 15.0


def registrar_uso(usage) -> None:
    USO["entrada"] += usage.input_tokens or 0
    USO["cache_escritura"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
    USO["cache_lectura"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    USO["salida"] += usage.output_tokens or 0
    USO["llamadas"] += 1


def costo_real() -> float:
    """Costo en US$ de lo consumido en esta corrida, según lo que reportó la API."""
    return (USO["entrada"] * PRECIO_ENTRADA + USO["cache_escritura"] * PRECIO_ENTRADA * 1.25
            + USO["cache_lectura"] * PRECIO_ENTRADA * 0.1 + USO["salida"] * PRECIO_SALIDA) / 1e6


def version_prompt() -> str:
    """Huella corta de las instrucciones del Agente 1: cambia si cambian los prompts (trazabilidad)."""
    import hashlib
    return hashlib.sha256((SYSTEM_PROMPT_SANITIZER + SYSTEM_PROMPT_ANALYST).encode("utf-8")).hexdigest()[:10]


def formatear_tickets(tickets: list) -> str:
    if not tickets:
        return "- Sin tickets en los últimos 180 días."
    return "\n".join(
        f"- {t['date_opened']}: {t['issue_type']} (estado: {t['status']}"
        f"{', resuelto ' + str(t['date_resolved']) if t.get('date_resolved') else ''})"
        for t in tickets
    )


def clean_and_parse_json(raw_text: str) -> dict:
    """
    Limpia y recupera objetos JSON aunque vengan envueltos en markdown
    o con llaves ligeramente desbalanceadas.
    """
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE).strip()

    # Intento 1: Parseo directo
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Intento 2: Buscar el primer objeto {...} balanceado
    start_idx = text.find("{")
    if start_idx != -1:
        depth = 0
        end_idx = -1
        for i in range(start_idx, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        if end_idx != -1:
            try:
                return json.loads(text[start_idx : end_idx + 1])
            except json.JSONDecodeError:
                pass

    # Intento 3: Reparar cierre si fue cortado por max_tokens
    if start_idx != -1 and "}" not in text[start_idx:]:
        patched = text[start_idx:] + '"\n}'
        try:
            return json.loads(patched)
        except json.JSONDecodeError:
            pass

    return {}


async def ejecutar_con_backoff(coro_func, *args, max_retries=4, initial_delay=1.5):
    """
    Ejecuta una función asíncrona con backoff exponencial ante errores transitorios
    (429, 5xx/529, fallas de conexión). Errores de autenticación o de request no se reintentan.
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return await coro_func(*args)
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise e
            print(f"  [{type(e).__name__}] Reintentando en {delay:.1f}s...")
            await asyncio.sleep(delay)
            delay *= 2


async def sanitizar_transcripcion(client: anthropic.AsyncAnthropic, transcript: str) -> str:
    """Fase 1: Sanitiza y genera diálogo limpio usando modelo optimizado."""
    async def _call():
        resp = await client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=600,
            system=SYSTEM_PROMPT_SANITIZER,
            messages=[{"role": "user", "content": f"Transcripción cruda:\n{transcript}"}],
        )
        registrar_uso(resp.usage)
        return resp.content[0].text.strip()

    try:
        return await ejecutar_con_backoff(_call)
    except Exception:
        # Fallback a texto original si falla la sanitización previa
        return transcript.strip()


def _top(valores: list, n: int = 3) -> str:
    conteo = {}
    for v in valores:
        conteo[v or "—"] = conteo.get(v or "—", 0) + 1
    return ", ".join(f"{k} ({c})" for k, c in sorted(conteo.items(), key=lambda x: -x[1])[:n])


def resumir_historial(compras: list, caso: dict) -> str:
    """
    Resumen determinista de cómo compra normalmente la tarjeta y qué tiene de nuevo la
    transacción reclamada (sin LLM y sin etiquetas de fraude).
    """
    if not compras:
        return "- Sin compras en los 90 días previos a la transacción reclamada."
    montos = [float(c["amount"]) for c in compras]
    promedio = sum(montos) / len(montos)
    lineas = [
        f"- {len(compras)} compras entre {str(compras[0]['txn_date'])[:10]} y {str(compras[-1]['txn_date'])[:10]}; "
        f"monto promedio S/ {promedio:,.2f}, máximo S/ {max(montos):,.2f}.",
        f"- Ciudades: {_top([c['ciudad_comercio'] for c in compras])}.",
        f"- Canales: {_top([c['canal'] for c in compras])}. Modos de entrada: {_top([c['modo_entrada'] for c in compras])}.",
        f"- Rubros: {_top([c['merchant_category'] for c in compras])}.",
    ]
    nuevos = []
    for campo, valor, nombre in [("ciudad_comercio", caso.get("txn_ciudad_comercio"), "ciudad"),
                                 ("canal", caso.get("txn_canal"), "canal"),
                                 ("modo_entrada", caso.get("txn_modo_entrada"), "modo de entrada"),
                                 ("merchant_category", caso.get("merchant_category"), "rubro")]:
        if valor and valor not in {c[campo] for c in compras}:
            nuevos.append(f"{nombre} {valor}")
    monto = float(caso.get("txn_amount") or 0)
    comparacion = f"el monto reclamado es {monto / promedio:.1f} veces el promedio" if promedio else ""
    lineas.append("- Frente a este historial, la transacción reclamada tiene por primera vez: "
                  + (", ".join(nuevos) if nuevos else "nada nuevo (ciudad, canal, modo y rubro ya usados)")
                  + (f"; {comparacion}." if comparacion else "."))
    return "\n".join(lineas)


def construir_contexto(caso: dict, dialogo_limpio: str, tickets: list, compras: list = None) -> str:
    """Texto exacto que recibe Claude en el análisis forense (se guarda para trazabilidad)."""
    return f"""--- DATOS TRANSACCIONALES VINCULADOS ---
- Canal de atención de la llamada (por dónde reportó el cliente): {caso.get('call_canal') or 'N/A'}
- Duración llamada: {caso.get('duracion_segundos') or 0} segundos
- Fecha/Hora de la llamada: {caso.get('fecha_llamada')}
- Comercio / Categoría: {caso.get('merchant_category') or 'No disponible'}
- Monto reclamado: S/ {caso.get('txn_amount') or 0.00}
- Fecha de la transacción: {caso.get('txn_date') or 'No disponible'}
- Canal de la transacción: {caso.get('txn_canal') or 'No disponible'} (POS = presencial, INT = internet, APP = app del banco)
- Modo de entrada: {caso.get('txn_modo_entrada') or 'No disponible'}
- Ciudad del comercio: {caso.get('txn_ciudad_comercio') or 'No disponible'}
- Tipo de tarjeta: {caso.get('card_type') or 'N/A'}
- Límite de crédito tarjeta: S/ {caso.get('credit_limit') or 'N/A'}
- Ciudad cliente: {caso.get('customer_city') or 'N/A'}
- Ocupación: {caso.get('customer_occupation') or 'N/A'}
- Ingreso anual: S/ {caso.get('customer_income') or 'N/A'}
- Score crediticio: {caso.get('customer_credit_score') or 'N/A'}

--- HISTORIAL DE TICKETS DEL CLIENTE (últimos 180 días) ---
{formatear_tickets(tickets)}

--- HISTORIAL DE COMPRAS DE LA TARJETA (90 días previos a la transacción reclamada) ---
{resumir_historial(compras or [], caso)}

--- DIÁLOGO LIMPIO DE LA LLAMADA ---
{dialogo_limpio}
"""


async def analizar_caso_forense(
    client: anthropic.AsyncAnthropic,
    caso: dict,
    dialogo_limpio: str,
    tickets: list,
    compras: list = None,
) -> dict:
    """Fase 2: Analista forense ciego con cruce de diálogo limpio, datos transaccionales, tickets e historial."""
    contexto_transaccional = construir_contexto(caso, dialogo_limpio, tickets, compras)

    async def _call():
        resp = await client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=4000,  # la ficha con evidencia citada puede pasar de 2500
            # Las instrucciones (con el Manual completo) son iguales en todos los casos: se guardan en caché
            # y desde el segundo caso se cobran a ~10 % del precio de entrada
            system=[{"type": "text", "text": SYSTEM_PROMPT_ANALYST, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": contexto_transaccional}],
        )
        registrar_uso(resp.usage)
        return resp.content[0].text.strip()

    for retry in range(2):
        raw_output = await ejecutar_con_backoff(_call)
        parsed = clean_and_parse_json(raw_output)
        if parsed and "ai_tipificacion" in parsed:
            # Asegurar campo dialogo_limpio en el payload
            parsed["ai_dialogo_limpio"] = dialogo_limpio
            return parsed

    # Fallback estructurado si no parsea
    return {
        "ai_razonamiento_oculto": "No se pudo estructurar la respuesta JSON.",
        "ai_dialogo_limpio": dialogo_limpio,
        "ai_resumen_caso": f"Motivo: Reclamo. Monto: S/ {caso.get('txn_amount')}. Canal: {caso.get('call_canal')}",
        "ai_resumen_caso_tags": "no_parseado; reclamo",
        "ai_tipificacion": "no_identificado",
        "ai_tipificacion_tags": "no_identificado",
        "ai_estado_tipificacion": "Existente",
        "ai_confianza": 0,
        "ai_insights": "Error de parseo en la respuesta del modelo.",
        "ai_insights_tags": "parse_error",
        "ai_accion_recomendada": "Revisión manual del caso.",
        "ai_accion_recomendada_tags": "revision_manual",
        "ai_blindaje": "IF DETAILS_INSUFFICIENT THEN ROUTE_TO_MANUAL_REVIEW",
        "ai_blindaje_tags": "manual_review",
    }
