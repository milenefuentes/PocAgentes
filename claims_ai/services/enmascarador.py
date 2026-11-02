"""
Enmascaramiento de datos sensibles antes de enviar un caso a la IA (propuesta, lámina 15:
"Enmascara DNI, tarjetas, correos, direcciones y teléfonos antes del análisis").

Es determinista (expresiones regulares, sin IA) y conserva montos, fechas y horas, que la
IA sí necesita para tipificar. Cada dato se reemplaza por una etiqueta como [TARJETA].
"""
import re

# Bordes de un número: no pegado a otro dígito ni a un separador decimal seguido de dígito
# (así "2,420.75" o "12345678.90" no se confunden con un DNI, pero "5678," al final de una frase sí cuenta)
ANTES = r"(?<!\d)(?<!\d[.,])"
DESPUES = r"(?!\d|[.,]\d)"

# El orden importa: primero lo más largo (tarjetas) para que un DNI no capture parte de una tarjeta
PATRONES = [
    ("CORREO", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    # 13 a 19 dígitos, con espacios o guiones opcionales entre grupos (número de tarjeta completo)
    ("TARJETA", re.compile(ANTES + r"(?:\d[ -]?){12,18}\d" + DESPUES)),
    # Celular peruano: 9 dígitos que empiezan con 9, con prefijo +51 opcional
    ("TELEFONO", re.compile(r"(?:\+?51[ -]?)?" + ANTES + r"9\d{2}[ -]?\d{3}[ -]?\d{3}" + DESPUES)),
    # Fijo de Lima con código entre paréntesis: (01) 234-5678
    ("TELEFONO", re.compile(r"\(0?1\)\s?\d{3}[ -]?\d{4}" + DESPUES)),
    # DNI: 8 dígitos sueltos, que no sean parte de un monto (1.234,56) ni de una fecha u hora
    ("DNI", re.compile(r"(?<![\d:/-])" + ANTES + r"\d{8}(?![:/-])" + DESPUES)),
    # Dirección: Av./Jr./Calle/Psje. + nombre + número
    ("DIRECCION", re.compile(
        r"\b(?:Av(?:enida)?\.?|Jr\.?|Jir[oó]n|Calle|Ca\.|Psje\.?|Pasaje|Mz\.?)\s+[A-ZÁÉÍÓÚÑa-záéíóúñ.\s]{2,40}?\s*"
        r"(?:N[°º.o]\s*)?\d{1,5}\b")),
]


def enmascarar(texto: str):
    """Devuelve (texto_enmascarado, conteo) donde conteo = {"TARJETA": 1, ...} solo con lo encontrado."""
    if not texto:
        return texto, {}
    conteo = {}
    for etiqueta, patron in PATRONES:
        texto, n = patron.subn(f"[{etiqueta}]", texto)
        if n:
            conteo[etiqueta] = conteo.get(etiqueta, 0) + n
    return texto, conteo
