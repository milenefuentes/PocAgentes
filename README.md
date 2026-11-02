# CLAIMS.AI – MVP de agentes de fraude

Prototipo de los dos primeros agentes de CLAIMS.AI para Interbank y su portal de validación humana.

- **Agente 1 – Fraud Analyst:** convierte cada llamada de reclamo en una ficha estructurada (diálogo limpio,
  tipificación según el Manual de Modalidades, hallazgos, acción recomendada, regla de blindaje y confianza),
  cruzando la transcripción con la transacción reclamada, el perfil del cliente y sus tickets.
- **Agente 2 – Pattern Investigator:** agrupa los casos (clustering), detecta anomalías frente al historial de
  cada tarjeta y terminales de riesgo, y propone los patrones e hipótesis más críticos. Las cifras se calculan
  con los datos y el LLM solo redacta la interpretación. Pattern Memory evita reproponer lo ya descartado.
- **Portal web:** resumen ejecutivo, recorrido de los datos, patrones, hipótesis, mapa de relaciones y el detalle
  de cada caso (qué recibió la IA y qué produjo). El analista valida, investiga o descarta cada hipótesis.
- **Detalle de caso** (`#caso/58`): ficha del Agente 1 con su variante (las 5 de la propuesta; hoy se calculan
  1 con tipología, 3 incompleto y 4 sin tipología), transcripción, movimientos de la tarjeta, cliente, flujo del caso
  e historial. El analista confirma o corrige la tipología, la marca como nueva modalidad o comenta; esa decisión
  queda en `revisiones_caso` y el Agente 2 usa la tipología del analista en su siguiente corrida.
- **Configuración** (`#config`): parámetros de negocio del Agente 2 (ventana de días, umbrales, modalidades
  excluidas) editables sin tocar código, catálogo de modalidades vigente y última corrida de cada agente.

Manual de Modalidades (`data/manual_modalidades.json`, v3.1): 8 modalidades con definición, indicadores, frases
típicas del cliente, criterios para diferenciar modalidades parecidas, riesgo base y el catálogo de casos que no son
fraude (NF01-NF05), tomados del manual de referencia `manual_modalidades_fraude_poc.json` (21 tipologías, aún no
conectado). El Agente 1 recibe el manual completo; esas instrucciones se guardan en la caché de la API, así que desde
el segundo caso de cada corrida se cobran a ~10 %, y al terminar se muestra el costo real. Respaldo de la v3.0 en `backups/`.

Trazabilidad y protección de datos:
- Antes de enviar un caso a la IA, el Agente 1 oculta DNI, tarjetas, correos, teléfonos y direcciones
  (`claims_ai/services/enmascarador.py`, sin IA). Cada ficha guarda su corrida, modelo, versión de las
  instrucciones, versión del Manual y qué datos se ocultaron.
- El Agente 2 clasifica cada patrón contra el catálogo (Manual): **Conocido** si coincide con una modalidad,
  **Nuevo** si no (o si la mayoría de sus casos fueron marcados como nueva modalidad) y **Bajo umbral** si tiene
  pocos casos. Cada corrida guarda los parámetros con los que se ejecutó.

Los datos son sintéticos. La IA (Claude) solo se usa al ejecutar los agentes; el portal no consume tokens.

## Uso diario

```bash
source .venv/bin/activate
python portal/server.py                    # portal en http://localhost:8080
```

Ejecutar los agentes (consumen tokens de la API de Anthropic):

```bash
python agente1_tipificador.py --limite 5   # analiza 5 llamadas pendientes (~US$ 0,04 c/u)
python agente1_tipificador.py --evaluar    # solo métricas, sin consumir tokens
python agente1_tipificador.py --reprocesar --estimar --todas  # costo de reprocesar, sin consumir tokens
python agente1_tipificador.py --reprocesar --ids 58 1         # vuelve a analizar esas llamadas
python agente1_tipificador.py --reprocesar --todas            # vuelve a analizar las hechas con otra versión
python agente2_patrones.py                 # nueva corrida de patrones (~US$ 0,08)
python agente2_patrones.py --ver           # patrones vigentes, sin consumir tokens
```

## Estructura

```
agente1_tipificador.py    Comando del Agente 1
agente2_patrones.py       Comando del Agente 2
portal/server.py          Comando del portal web

claims_ai/                El sistema, organizado en Modelo-Vista-Controlador
  config.py                 .env, modelo de Claude y rutas
  models/                   Acceso a datos (PostgreSQL, Manual de Modalidades)
  services/                 Lógica de cada agente (análisis, clustering, Pattern Memory)
  views/                    Consola y portal web (HTML, JS, CSS)
  controllers/              Orquestación de cada flujo y rutas HTTP

db/                       Base de datos
  schema.sql                Esquema completo
  migrate_db.py             Migraciones (idempotentes, solo agregan)
  compose.yaml              PostgreSQL local con Podman (plan B)

scripts/                  Herramientas para regenerar los datos de prueba
data/                     CSV de datos sintéticos y Manual de Modalidades
backups/                  Respaldos de la base y del código
```

## Base de datos

El archivo `.env` apunta a **Neon** (PostgreSQL en la nube). `.env.local` contiene la configuración de la
base local en Podman, por si no hay acceso a internet:

```bash
cp .env.local .env               # usar la base local
podman start postgres-agents     # encender el contenedor
```

Después de cambiar el esquema: `python db/migrate_db.py`.

## Regenerar los datos de prueba desde cero

Solo si se parte de una base vacía (los pasos de carga insertan datos):

```bash
python scripts/generate_data.py            # CSV de clientes, cuentas, tarjetas, transacciones, etc.
python scripts/generate_calls.py           # llamadas de reclamo con su modalidad real
psql ... -f db/schema.sql                  # crear las tablas
python scripts/load_data.py                # cargar los CSV
python scripts/load_llamadas.py            # cargar las llamadas
python db/migrate_db.py                    # columnas y tablas de versiones posteriores
python scripts/enriquecer_transacciones.py # canal, modo de entrada, terminal y patrones plantados
```

Si cambia la fórmula de confianza, de prioridad o de clasificación del Agente 2, se puede aplicar a la corrida vigente
sin volver a ejecutar el agente (no consume tokens):

```bash
python scripts/recalcular_confianza_prioridad.py
```
