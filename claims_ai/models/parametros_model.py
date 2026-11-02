"""
Parámetros de negocio del Agente 2 (tabla parametros_negocio). Se editan desde el portal,
sin tocar código, y cada corrida guarda los valores con los que se ejecutó.
"""
from claims_ai.models.manual_model import MODALIDADES_VALIDAS

# clave -> (tipo, mínimo, máximo); las listas se guardan como texto separado por comas
TIPOS = {
    "ventana_dias": (int, 0, 3650),
    "min_casos_patron": (int, 2, 1000),
    "min_casos_nuevos_evidencia": (int, 1, 1000),
    "min_casos_entidad": (int, 2, 1000),
    "umbral_impacto_alto": (float, 0, 1e9),
    "umbral_impacto_medio": (float, 0, 1e9),
    "modalidades_excluidas": (list, None, None),
}


def convertir(clave: str, texto: str):
    """Convierte y valida el valor de un parámetro; lanza ValueError si no es válido."""
    if clave not in TIPOS:
        raise ValueError(f"Parámetro desconocido: {clave}")
    tipo, minimo, maximo = TIPOS[clave]
    texto = (texto or "").strip()
    if tipo is list:
        valores = [v.strip() for v in texto.split(",") if v.strip()]
        invalidos = [v for v in valores if v not in MODALIDADES_VALIDAS]
        if invalidos:
            raise ValueError(f"Modalidades que no están en el Manual: {', '.join(invalidos)}")
        return valores
    try:
        valor = tipo(texto)
    except ValueError:
        raise ValueError(f"{clave} debe ser un número") from None
    if not minimo <= valor <= maximo:
        raise ValueError(f"{clave} debe estar entre {minimo:g} y {maximo:g}")
    return valor


def listar_parametros(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT clave, valor, descripcion, actualizado_por, updated_at
            FROM parametros_negocio;
        """)
        orden = list(TIPOS)   # orden lógico: ventana, umbrales de patrón, entidades, impacto, exclusiones
        return sorted(cur.fetchall(), key=lambda p: orden.index(p["clave"]) if p["clave"] in orden else len(orden))


def cargar_parametros(conn) -> dict:
    """Parámetros ya convertidos a su tipo, listos para el Agente 2."""
    return {p["clave"]: convertir(p["clave"], p["valor"]) for p in listar_parametros(conn)}


def guardar_parametros(conn, cambios: dict, analista: str) -> dict:
    """Valida todos los cambios y los guarda juntos (si uno no es válido, no se guarda ninguno)."""
    nuevos = {**cargar_parametros(conn), **{c: convertir(c, v) for c, v in cambios.items()}}
    if nuevos["umbral_impacto_medio"] > nuevos["umbral_impacto_alto"]:
        raise ValueError("El umbral de impacto medio no puede ser mayor que el alto")
    try:
        with conn.cursor() as cur:
            for clave, valor in cambios.items():
                texto = ",".join(nuevos[clave]) if isinstance(nuevos[clave], list) else str(nuevos[clave])
                cur.execute("""
                    UPDATE parametros_negocio
                    SET valor = %s, actualizado_por = %s, updated_at = NOW()
                    WHERE clave = %s;
                """, (texto, analista, clave))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return nuevos
