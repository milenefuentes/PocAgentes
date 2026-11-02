-- Esquema superbank_db
-- Ejecutar dentro de psql conectado a superbank_db

DROP TABLE IF EXISTS patrones CASCADE;
DROP TABLE IF EXISTS tipificaciones CASCADE;
DROP TABLE IF EXISTS llamadas CASCADE;
DROP TABLE IF EXISTS support_tickets CASCADE;
DROP TABLE IF EXISTS loan_payments CASCADE;
DROP TABLE IF EXISTS loans CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS card_transactions CASCADE;
DROP TABLE IF EXISTS cards CASCADE;
DROP TABLE IF EXISTS accounts CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS employees CASCADE;
DROP TABLE IF EXISTS branches CASCADE;

-- 1. Sucursales
CREATE TABLE branches (
    branch_id     SERIAL PRIMARY KEY,
    branch_name   VARCHAR(100) NOT NULL,
    city          VARCHAR(100),
    state         VARCHAR(100),
    opened_date   DATE,
    ifsc_code     VARCHAR(20)
);

-- 2. Empleados
CREATE TABLE employees (
    employee_id   SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    branch_id     INTEGER REFERENCES branches(branch_id),
    role          VARCHAR(50),
    hire_date     DATE,
    salary        NUMERIC(10,2)
);

-- 3. Clientes
CREATE TABLE customers (
    customer_id    SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    gender         VARCHAR(20),
    date_of_birth  DATE,
    city           VARCHAR(100),
    state          VARCHAR(100),
    phone          VARCHAR(20),
    email          VARCHAR(150),
    occupation     VARCHAR(100),
    annual_income  NUMERIC(12,2),
    join_date      DATE,
    credit_score   INTEGER
);

-- 4. Cuentas
CREATE TABLE accounts (
    account_id     SERIAL PRIMARY KEY,
    customer_id    INTEGER REFERENCES customers(customer_id),
    branch_id      INTEGER REFERENCES branches(branch_id),
    account_type   VARCHAR(50),
    balance        NUMERIC(14,2),
    open_date      DATE,
    status         VARCHAR(20)
);

-- 5. Tarjetas
CREATE TABLE cards (
    card_id        SERIAL PRIMARY KEY,
    customer_id    INTEGER REFERENCES customers(customer_id),
    account_id     INTEGER REFERENCES accounts(account_id),
    card_type      VARCHAR(50),
    issue_date     DATE,
    expiry_date    DATE,
    credit_limit   NUMERIC(12,2),
    status         VARCHAR(20)
);

-- 6. Transacciones de tarjeta
CREATE TABLE card_transactions (
    card_txn_id       SERIAL PRIMARY KEY,
    card_id           INTEGER REFERENCES cards(card_id),
    txn_date          TIMESTAMP,
    merchant_category VARCHAR(100),
    amount            NUMERIC(12,2),
    is_fraud          BOOLEAN DEFAULT FALSE,
    -- v3.2: contexto transaccional (lo completa enriquecer_transacciones.py)
    canal             VARCHAR(10),   -- POS / INT / APP (codigos del Manual de Modalidades)
    modo_entrada      VARCHAR(15),   -- CHIP / CONTACTLESS / BANDA / FALLBACK / NO_PRESENTE / APP
    terminal_id       VARCHAR(30),   -- terminal POS o ID de comercio / pasarela online
    ciudad_comercio   VARCHAR(60),   -- ciudad donde ocurrio la transaccion ('Online' si es digital)
    es_plantado       BOOLEAN DEFAULT FALSE  -- fila insertada o asignada a un patron de prueba conocido
);

-- 7. Transacciones de cuenta
CREATE TABLE transactions (
    transaction_id    SERIAL PRIMARY KEY,
    account_id        INTEGER REFERENCES accounts(account_id),
    txn_date          TIMESTAMP,
    txn_type          VARCHAR(50),
    amount             NUMERIC(12,2),
    channel            VARCHAR(50),
    merchant_category  VARCHAR(100)
);

-- 8. PrÃ©stamos
CREATE TABLE loans (
    loan_id        SERIAL PRIMARY KEY,
    customer_id    INTEGER REFERENCES customers(customer_id),
    branch_id      INTEGER REFERENCES branches(branch_id),
    loan_type      VARCHAR(50),
    loan_amount    NUMERIC(14,2),
    interest_rate  NUMERIC(5,2),
    term_months    INTEGER,
    start_date     DATE,
    status         VARCHAR(20)
);

-- 9. Pagos de prÃ©stamos
CREATE TABLE loan_payments (
    payment_id            SERIAL PRIMARY KEY,
    loan_id               INTEGER REFERENCES loans(loan_id),
    payment_date          DATE,
    amount_paid           NUMERIC(12,2),
    principal_component   NUMERIC(12,2),
    interest_component    NUMERIC(12,2),
    late_payment_flag     BOOLEAN DEFAULT FALSE
);

-- 10. Tickets de soporte
CREATE TABLE support_tickets (
    ticket_id           SERIAL PRIMARY KEY,
    customer_id         INTEGER REFERENCES customers(customer_id),
    issue_type          VARCHAR(100),
    date_opened         DATE,
    date_resolved       DATE,
    status              VARCHAR(20),
    satisfaction_score  INTEGER
);

-- 11. Llamadas / reclamos (insumo para el Agente 1 - "Entender el caso")
CREATE TABLE llamadas (
    call_id             SERIAL PRIMARY KEY,
    customer_id         INTEGER REFERENCES customers(customer_id),
    card_txn_id         INTEGER REFERENCES card_transactions(card_txn_id),
    canal               VARCHAR(30),
    fecha_llamada       TIMESTAMP,
    duracion_segundos   INTEGER,
    transcript          TEXT,
    modalidad_real      VARCHAR(50)   -- etiqueta real, solo para evaluar al agente (no la ve el LLM en produccion)
);

-- 12. Tipificaciones generadas por el Agente 1 ("Entender el caso - Claims AI v3.0")
CREATE TABLE tipificaciones (
    tipificacion_id            SERIAL PRIMARY KEY,
    call_id                    INTEGER REFERENCES llamadas(call_id),
    modalidad_predicha         VARCHAR(50),
    confianza                  VARCHAR(20),   -- alta / media / baja (derivada de confianza_score)
    confianza_score            NUMERIC(5,2),  -- 0-100, confianza declarada por el LLM en su tipificacion
    justificacion              TEXT,
    correcta                   BOOLEAN,       -- comparado contra modalidad_real, solo para evaluacion
    ai_razonamiento_oculto     TEXT,
    ai_dialogo_limpio          TEXT,
    ai_resumen_caso            TEXT,
    ai_resumen_caso_tags       TEXT,
    ai_tipificacion            VARCHAR(100),
    ai_tipificacion_tags       TEXT,
    ai_estado_tipificacion     VARCHAR(50),
    ai_insights                TEXT,
    ai_insights_tags           TEXT,
    ai_accion_recomendada      TEXT,
    ai_accion_recomendada_tags TEXT,
    ai_blindaje                TEXT,
    ai_blindaje_tags           TEXT,
    raw_payload                JSONB,
    ai_contexto_entrada        TEXT,          -- texto exacto enviado a Claude (v3.3)
    created_at                 TIMESTAMP DEFAULT NOW()
);

-- 12b. Corridas del Agente 2: cada ejecucion queda versionada (no se borra historia)
CREATE TABLE IF NOT EXISTS corridas_patrones (
    corrida_id    SERIAL PRIMARY KEY,
    n_casos       INTEGER,
    k_clusters    INTEGER,
    silhouette    NUMERIC(6,3),
    created_at    TIMESTAMP DEFAULT NOW()
);

-- 13. Patrones descubiertos por el Agente 2 ("Pattern Investigator - Claims AI v3.0")
-- num_casos, impacto_total, tasa_sospecha y casos_asociados se calculan en codigo, no los escribe el LLM.
CREATE TABLE patrones (
    patron_id                 SERIAL PRIMARY KEY,
    titulo                    VARCHAR(150),
    modalidad_asociada        VARCHAR(50),
    descripcion               TEXT,
    evidencia                 TEXT,   -- resumen de los datos que soportan el patron
    num_casos                 INTEGER,
    regla_propuesta           TEXT,   -- regla de monitoreo sugerida (insumo para el Agente 3/4)
    ai_razonamiento_oculto    TEXT,
    analisis_reglas           TEXT,
    distribuciones            TEXT,
    analisis_tipificaciones   TEXT,
    accion_recomendada        TEXT,
    cluster_id                INTEGER,
    clusters_origen           JSONB,  -- lista de clusters que cubre el patron
    tasa_sospecha             NUMERIC(5,2),  -- % de casos con confianza_score A1 >= 80
    impacto_total             NUMERIC(14,2),
    casos_asociados           JSONB,
    raw_payload               JSONB,
    corrida_id                INTEGER REFERENCES corridas_patrones(corrida_id),
    vigente                   BOOLEAN DEFAULT TRUE,  -- solo la ultima corrida queda vigente
    created_at                TIMESTAMP DEFAULT NOW()
);

-- 13b. Hipotesis propuestas por el Agente 2 para validacion humana (una por patron)
-- confianza = 0.5 * confianza media A1 + 0.3 * pureza de modalidad + 0.2 * soporte (n casos / 10)
CREATE TABLE IF NOT EXISTS hipotesis (
    hipotesis_id          SERIAL PRIMARY KEY,
    corrida_id            INTEGER REFERENCES corridas_patrones(corrida_id),
    patron_id             INTEGER REFERENCES patrones(patron_id),
    enunciado             TEXT,
    modalidad             VARCHAR(100),
    confianza             NUMERIC(5,2),
    confianza_detalle     JSONB,
    impacto_monto         NUMERIC(14,2),
    nivel_impacto         VARCHAR(10),   -- Alto / Medio / Bajo
    num_casos             INTEGER,
    casos_asociados       JSONB,
    evidencia             JSONB,         -- entidades dominantes que soportan la hipotesis
    estado                VARCHAR(30) DEFAULT 'Pendiente revisión',
    comentario_analista   TEXT,
    analista              VARCHAR(100),
    fecha_revision        TIMESTAMP,
    vigente               BOOLEAN DEFAULT TRUE,
    created_at            TIMESTAMP DEFAULT NOW()
);

-- 13c. Bitacora de revisiones humanas (trazabilidad: cada cambio de estado queda registrado)
CREATE TABLE IF NOT EXISTS revisiones_hipotesis (
    revision_id     SERIAL PRIMARY KEY,
    hipotesis_id    INTEGER REFERENCES hipotesis(hipotesis_id),
    estado          VARCHAR(30),
    comentario      TEXT,
    analista        VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- 13e. v3.4: Pattern Memory (memoria simple, no vectorizada, sin auto-entrenamiento).
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

-- 13f. v3.5: prioridad de la hipótesis (separada de la confianza)
ALTER TABLE hipotesis
  ADD COLUMN IF NOT EXISTS prioridad VARCHAR(10),          -- Alta / Media / Baja
  ADD COLUMN IF NOT EXISTS prioridad_detalle JSONB;        -- puntos que la explican

-- 13g. v3.6: revisión humana de cada ficha del Agente 1 (confirmar, corregir, nueva modalidad, comentar)
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

-- 13d. Relaciones entre entidades (co-ocurrencias en los casos tipificados por A1)
CREATE TABLE IF NOT EXISTS relaciones_entidades (
    relacion_id     SERIAL PRIMARY KEY,
    corrida_id      INTEGER REFERENCES corridas_patrones(corrida_id),
    origen_tipo     VARCHAR(30),
    origen          VARCHAR(150),
    destino_tipo    VARCHAR(30),
    destino         VARCHAR(150),
    peso            INTEGER,     -- numero de casos que comparten ambas entidades
    casos           JSONB,
    clusters        JSONB
);

-- 14. Validaciones generadas por el Agente 3 ("Validar el patron")
-- Contrasta cada patron del Agente 2 contra transacciones reales.
CREATE TABLE IF NOT EXISTS validaciones (
    validacion_id           SERIAL PRIMARY KEY,
    patron_id                INTEGER REFERENCES patrones(patron_id),
    score                     INTEGER,        -- 0-100, combina lift + tamano de muestra
    n_transacciones_match     INTEGER,        -- cuantas transacciones reales cumplen el patron
    tasa_fraude_patron        NUMERIC(6,2),   -- % de fraude dentro del patron
    tasa_fraude_base          NUMERIC(6,2),   -- % de fraude general (fuera del patron)
    lift                      NUMERIC(8,2),   -- tasa_fraude_patron / tasa_fraude_base
    criterios_usados          JSONB,          -- filtros extraidos del patron (comercio, ciudad, etc.)
    ejemplos                  JSONB,          -- hasta 5 transacciones de ejemplo que matchean
    no_validable              BOOLEAN DEFAULT FALSE,  -- true si la extraccion no encontro criterios concretos
    created_at                TIMESTAMP DEFAULT NOW()
);
 



-- 15. Reglas de monitoreo generadas por el Agente 4 ("Convertir en regla")
-- Traduce cada patron ya validado (Agente 3) en una regla formal, lista
-- para que el equipo de riesgo la evalue.
CREATE TABLE IF NOT EXISTS reglas (
    regla_id        SERIAL PRIMARY KEY,
    patron_id        INTEGER REFERENCES patrones(patron_id),
    validacion_id     INTEGER REFERENCES validaciones(validacion_id),
    titulo_regla       VARCHAR(150),
    condicion            TEXT,   -- condicion formal evaluable (comercio, ciudad, tipo tarjeta, montos)
    accion                TEXT,  -- accion concreta a tomar (bloquear, alertar, pedir 2FA, etc.)
    prioridad              VARCHAR(20),  -- critica / alta / media / baja
    justificacion            TEXT,  -- basada en los numeros reales de la validacion
    created_at                TIMESTAMP DEFAULT NOW()
);
 

 -- 16. Backtesting generado por el Agente 5 ("Medir efectividad")
-- Simula cada regla contra el historico completo de transacciones, antes
-- de implementarla en produccion.
CREATE TABLE IF NOT EXISTS backtesting (
    backtesting_id      SERIAL PRIMARY KEY,
    regla_id              INTEGER REFERENCES reglas(regla_id),
    n_match                 INTEGER,   -- transacciones que la regla hubiera marcado
    verdaderos_positivos      INTEGER, -- de esas, cuantas eran fraude real
    falsos_positivos            INTEGER, -- de esas, cuantas eran clientes legitimos
    precision_pct                  NUMERIC(6,2),  -- verdaderos_positivos / n_match
    recall_pct                        NUMERIC(6,2),  -- verdaderos_positivos / fraude total en la base
    created_at                          TIMESTAMP DEFAULT NOW()
);

-- 13h. v3.7: trazabilidad del Agente 1 por corrida y parámetros de negocio configurables (propuesta, láminas 15-18 y 78)
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

-- 13i. v3.8: fichas 2 (no fraude) y 5 (transcripción ilegible), evidencia citada y reprocesamiento con historial
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
