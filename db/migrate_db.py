"""
Migracion de la base de datos para Claims AI v3.0 - v3.6
Agrega columnas ampliadas a la tabla `tipificaciones` y las tablas de
versionado, hipotesis, revision humana y relaciones del Agente 2.
Es idempotente y solo agrega (no borra ni modifica datos existentes).
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)  # permite importar claims_ai desde scripts/ o db/

from claims_ai.models import database  # noqa: E402


def _connect():
    return database.connect(dict_rows=False)

MIGRATION_SQL = """
ALTER TABLE tipificaciones
  ADD COLUMN IF NOT EXISTS ai_razonamiento_oculto TEXT,
  ADD COLUMN IF NOT EXISTS ai_dialogo_limpio TEXT,
  ADD COLUMN IF NOT EXISTS ai_resumen_caso TEXT,
  ADD COLUMN IF NOT EXISTS ai_resumen_caso_tags TEXT,
  ADD COLUMN IF NOT EXISTS ai_tipificacion VARCHAR(100),
  ADD COLUMN IF NOT EXISTS ai_tipificacion_tags TEXT,
  ADD COLUMN IF NOT EXISTS ai_estado_tipificacion VARCHAR(50),
  ADD COLUMN IF NOT EXISTS ai_insights TEXT,
  ADD COLUMN IF NOT EXISTS ai_insights_tags TEXT,
  ADD COLUMN IF NOT EXISTS ai_accion_recomendada TEXT,
  ADD COLUMN IF NOT EXISTS ai_accion_recomendada_tags TEXT,
  ADD COLUMN IF NOT EXISTS ai_blindaje TEXT,
  ADD COLUMN IF NOT EXISTS ai_blindaje_tags TEXT,
  ADD COLUMN IF NOT EXISTS raw_payload JSONB,
  ADD COLUMN IF NOT EXISTS confianza_score NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS ai_contexto_entrada TEXT;

-- v3.1: versionado de corridas de A2, hipotesis, bitacora de revision y relaciones
CREATE TABLE IF NOT EXISTS corridas_patrones (
    corrida_id    SERIAL PRIMARY KEY,
    n_casos       INTEGER,
    k_clusters    INTEGER,
    silhouette    NUMERIC(6,3),
    created_at    TIMESTAMP DEFAULT NOW()
);

ALTER TABLE patrones
  ADD COLUMN IF NOT EXISTS clusters_origen JSONB,
  ADD COLUMN IF NOT EXISTS corrida_id INTEGER REFERENCES corridas_patrones(corrida_id),
  ADD COLUMN IF NOT EXISTS vigente BOOLEAN DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS hipotesis (
    hipotesis_id          SERIAL PRIMARY KEY,
    corrida_id            INTEGER REFERENCES corridas_patrones(corrida_id),
    patron_id             INTEGER REFERENCES patrones(patron_id),
    enunciado             TEXT,
    modalidad             VARCHAR(100),
    confianza             NUMERIC(5,2),
    confianza_detalle     JSONB,
    impacto_monto         NUMERIC(14,2),
    nivel_impacto         VARCHAR(10),
    num_casos             INTEGER,
    casos_asociados       JSONB,
    evidencia             JSONB,
    estado                VARCHAR(30) DEFAULT 'Pendiente revisión',
    comentario_analista   TEXT,
    analista              VARCHAR(100),
    fecha_revision        TIMESTAMP,
    vigente               BOOLEAN DEFAULT TRUE,
    created_at            TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS revisiones_hipotesis (
    revision_id     SERIAL PRIMARY KEY,
    hipotesis_id    INTEGER REFERENCES hipotesis(hipotesis_id),
    estado          VARCHAR(30),
    comentario      TEXT,
    analista        VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- v3.2: contexto transaccional para el Manual de Modalidades y deteccion de anomalias
ALTER TABLE card_transactions
  ADD COLUMN IF NOT EXISTS canal VARCHAR(10),
  ADD COLUMN IF NOT EXISTS modo_entrada VARCHAR(15),
  ADD COLUMN IF NOT EXISTS terminal_id VARCHAR(30),
  ADD COLUMN IF NOT EXISTS ciudad_comercio VARCHAR(60),
  ADD COLUMN IF NOT EXISTS es_plantado BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_card_txn_terminal ON card_transactions (terminal_id);
CREATE INDEX IF NOT EXISTS idx_card_txn_card_fecha ON card_transactions (card_id, txn_date);

-- v3.4: Pattern Memory (memoria simple, no vectorizada, sin auto-entrenamiento).
-- Registra cada patrón detectado y la última decisión del analista, para no reproponer
-- patrones descartados sin evidencia nueva y clasificar cada hallazgo como nuevo / conocido / bajo umbral.
CREATE TABLE IF NOT EXISTS memoria_patrones (
    memoria_id        SERIAL PRIMARY KEY,
    firma             VARCHAR(200),   -- 'entidad:<terminal>' o 'cluster:<modalidad>'
    tipo              VARCHAR(20),    -- entidad / cluster
    modalidad         VARCHAR(100),
    entidad_id        VARCHAR(60),
    titulo            VARCHAR(150),
    rasgos            JSONB,          -- variables discriminantes de la última detección
    casos_vistos      JSONB,          -- unión de call_ids vistos en todas las detecciones
    num_casos         INTEGER,
    impacto_total     NUMERIC(14,2),
    confianza         NUMERIC(5,2),
    primera_corrida   INTEGER,
    ultima_corrida    INTEGER,
    veces_detectado   INTEGER DEFAULT 1,
    estado            VARCHAR(30) DEFAULT 'Pendiente revisión',  -- última decisión humana
    comentario        TEXT,
    analista          VARCHAR(100),
    fecha_decision    TIMESTAMP,
    modelo            VARCHAR(60),
    prompt_version    VARCHAR(20),
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW()
);

ALTER TABLE hipotesis
  ADD COLUMN IF NOT EXISTS memoria_id INTEGER REFERENCES memoria_patrones(memoria_id),
  ADD COLUMN IF NOT EXISTS clasificacion VARCHAR(30),       -- Nuevo / Conocido / Bajo umbral
  ADD COLUMN IF NOT EXISTS casos_nuevos INTEGER,            -- casos no vistos en detecciones previas
  ADD COLUMN IF NOT EXISTS decision_heredada BOOLEAN DEFAULT FALSE;

ALTER TABLE corridas_patrones
  ADD COLUMN IF NOT EXISTS omitidos_memoria JSONB,          -- patrones descartados que no se repropusieron
  ADD COLUMN IF NOT EXISTS modelo VARCHAR(60),
  ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(20);

-- v3.5: prioridad de la hipótesis (separada de la confianza)
ALTER TABLE hipotesis
  ADD COLUMN IF NOT EXISTS prioridad VARCHAR(10),          -- Alta / Media / Baja
  ADD COLUMN IF NOT EXISTS prioridad_detalle JSONB;        -- puntos que la explican

-- v3.6: revisión humana de cada ficha del Agente 1 (confirmar, corregir, nueva modalidad, comentar)
ALTER TABLE tipificaciones
  ADD COLUMN IF NOT EXISTS estado_revision VARCHAR(30) DEFAULT 'Pendiente',  -- Pendiente / Confirmada / Corregida / Nueva modalidad
  ADD COLUMN IF NOT EXISTS tipologia_analista VARCHAR(100),                -- tipología que fija el analista (alimenta al Agente 2)
  ADD COLUMN IF NOT EXISTS analista_revision VARCHAR(100),
  ADD COLUMN IF NOT EXISTS fecha_revision TIMESTAMP;

CREATE TABLE IF NOT EXISTS revisiones_caso (
    revision_id        SERIAL PRIMARY KEY,
    call_id            INTEGER REFERENCES llamadas(call_id),
    accion             VARCHAR(30),     -- Confirmar / Corregir / Nueva modalidad / Comentario
    tipologia_anterior VARCHAR(100),
    tipologia_nueva    VARCHAR(100),
    comentario         TEXT,
    analista           VARCHAR(100),
    created_at         TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS relaciones_entidades (
    relacion_id     SERIAL PRIMARY KEY,
    corrida_id      INTEGER REFERENCES corridas_patrones(corrida_id),
    origen_tipo     VARCHAR(30),
    origen          VARCHAR(150),
    destino_tipo    VARCHAR(30),
    destino         VARCHAR(150),
    peso            INTEGER,
    casos           JSONB,
    clusters        JSONB
);

-- v3.7: trazabilidad del Agente 1 por corrida y parámetros de negocio configurables (propuesta, láminas 15-18 y 78)
CREATE TABLE IF NOT EXISTS corridas_agente1 (
    corrida_a1_id   SERIAL PRIMARY KEY,
    inicio          TIMESTAMP DEFAULT NOW(),
    fin             TIMESTAMP,
    n_casos         INTEGER,
    n_fallidos      INTEGER,
    modelo          VARCHAR(60),
    prompt_version  VARCHAR(20),
    version_manual  VARCHAR(20)
);

ALTER TABLE tipificaciones
  ADD COLUMN IF NOT EXISTS corrida_a1_id INTEGER REFERENCES corridas_agente1(corrida_a1_id),
  ADD COLUMN IF NOT EXISTS version_manual VARCHAR(20),      -- versión del Manual de Modalidades usada en la ficha
  ADD COLUMN IF NOT EXISTS modelo VARCHAR(60),
  ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(20),
  ADD COLUMN IF NOT EXISTS datos_enmascarados JSONB;        -- cuántos datos sensibles se ocultaron antes de la IA

-- Parámetros de negocio del Agente 2: se cambian desde el portal, sin tocar código
CREATE TABLE IF NOT EXISTS parametros_negocio (
    clave        VARCHAR(50) PRIMARY KEY,
    valor        VARCHAR(500) NOT NULL,
    descripcion  TEXT,
    actualizado_por VARCHAR(100),
    updated_at   TIMESTAMP DEFAULT NOW()
);

INSERT INTO parametros_negocio (clave, valor, descripcion) VALUES
  ('ventana_dias', '0', 'Días hacia atrás de fichas que toma el Agente 2 (30, 60 o 90 según la propuesta; 0 = todas)'),
  ('min_casos_patron', '5', 'Mínimo de casos para proponer un patrón; por debajo queda como Bajo umbral'),
  ('min_casos_nuevos_evidencia', '3', 'Casos nuevos necesarios para volver a proponer un patrón descartado'),
  ('min_casos_entidad', '3', 'Mínimo de víctimas para marcar un comercio como sospechoso'),
  ('umbral_impacto_alto', '15000', 'Monto (S/) desde el que el impacto de un patrón es Alto'),
  ('umbral_impacto_medio', '5000', 'Monto (S/) desde el que el impacto de un patrón es Medio'),
  ('modalidades_excluidas', '', 'Modalidades que el Agente 2 no considera (separadas por coma)')
ON CONFLICT (clave) DO NOTHING;

-- Cada corrida del Agente 2 guarda los parámetros con los que se ejecutó
ALTER TABLE corridas_patrones
  ADD COLUMN IF NOT EXISTS parametros JSONB;

-- v3.8: fichas 2 (no fraude) y 5 (transcripción ilegible), evidencia citada y reprocesamiento con historial
ALTER TABLE tipificaciones
  ADD COLUMN IF NOT EXISTS ai_situacion VARCHAR(20),        -- fraude / no_fraude / ilegible (lo decide el Agente 1)
  ADD COLUMN IF NOT EXISTS ai_motivo_situacion TEXT,        -- por qué no es fraude o qué tramos no se entienden
  ADD COLUMN IF NOT EXISTS ai_evidencia JSONB;              -- frases textuales de la llamada que sustentan la ficha

-- Al reprocesar un caso, la ficha anterior se archiva completa aquí (no se pierde historia)
CREATE TABLE IF NOT EXISTS tipificaciones_historial (
    historial_id     SERIAL PRIMARY KEY,
    call_id          INTEGER REFERENCES llamadas(call_id),
    tipificacion_id  INTEGER,
    ficha            JSONB NOT NULL,         -- la fila completa de tipificaciones tal como estaba
    archivada_en     TIMESTAMP DEFAULT NOW(),
    motivo           VARCHAR(100)
);
"""

def main():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
        conn.commit()
        print("Migracion v3.8 ejecutada exitosamente.")
        
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'tipificaciones'
            ORDER BY ordinal_position;
        """)
        cols = cur.fetchall()
        print("\nColumnas actuales de tipificaciones:")
        for name, dtype in cols:
            print(f"  - {name} ({dtype})")
    conn.close()

if __name__ == "__main__":
    main()
