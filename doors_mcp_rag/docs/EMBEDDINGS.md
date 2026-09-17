# Capa de embeddings

## Objetivo

La copia SQLite de DOORS puede almacenar un vector semántico por requisito. El cálculo se hace mediante un servidor que implemente una API compatible con OpenAI, pero el repositorio no fija todavía ni endpoint ni modelo.

## Columnas

La tabla `requirements` incorpora:

```sql
unique_identifier TEXT,
embedding BLOB,
embedding_model TEXT,
embedding_dimensions INTEGER,
embedding_content_hash TEXT,
embedding_updated_at TEXT
```

`unique_identifier` procede de `REM_UniqueIdentifier` y puede ser `NULL`.

El vector se serializa como `float32` little-endian en un BLOB. No se devuelve el BLOB a los agentes MCP; las respuestas exponen únicamente si existe, el modelo, la dimensión y la fecha.

## Migración

`RequirementsRepository.initialise()` inspecciona `PRAGMA table_info(requirements)` y añade las columnas que falten con `ALTER TABLE`. Por tanto, una base creada por versiones anteriores se actualiza sin tener que borrarla.

## Invalidez del vector

El embedding queda asociado a:

```text
embedding_model
embedding_content_hash
```

Cuando cambia el contenido del requisito, `upsert_requirement()` pone el embedding y sus metadatos a `NULL`. La siguiente ejecución del pipeline lo recalculará.

También se considera pendiente un requisito si cambia el modelo configurado.

## Configuración del endpoint

```text
DOORS_EMBEDDING_BASE_URL
DOORS_EMBEDDING_MODEL
DOORS_EMBEDDING_API_KEY
DOORS_EMBEDDING_BATCH_SIZE
DOORS_EMBEDDING_TIMEOUT_SECONDS
DOORS_EMBEDDING_MAX_INPUT_CHARS
```

Ejemplo:

```powershell
$env:DOORS_EMBEDDING_BASE_URL = "http://localhost:8000/v1"
$env:DOORS_EMBEDDING_MODEL = "mi-modelo-embedding"
$env:DOORS_EMBEDDING_API_KEY = ""
```

El SDK se inicializa conceptualmente como:

```python
OpenAI(
    base_url=config.base_url,
    api_key=config.api_key or "not-required",
)
```

y usa el endpoint compatible de embeddings mediante:

```python
client.embeddings.create(
    model=config.model,
    input=[...],
)
```

## Flujo incremental

```text
DOORS
  │
  ▼
sync_module()
  │
  ├── inserted ─────┐
  ├── updated ──────┼── embedding NULL
  └── unchanged ────┘       │
                             ▼
                    generate_embeddings()
                             │
                  OpenAI-compatible server
                             │
                             ▼
                       embedding BLOB
```

Los requisitos cuyo vector ya corresponde al mismo `content_hash` y modelo se omiten.

## Texto canónico

El texto enviado al modelo contiene, cuando están disponibles:

```text
Module
UniqueIdentifier
DOORS Identifier
Outline
Heading
Text
Attribute <nombre>: <valor>
```

El máximo se controla con `DOORS_EMBEDDING_MAX_INPUT_CHARS`.

## Búsqueda

La primera implementación usa similitud coseno en Python:

```text
query
  │
  ▼
embedding endpoint
  │
  ▼
query vector
  │
  ├── coseno vs requisito 1
  ├── coseno vs requisito 2
  ├── ...
  ▼
top-k
```

Es deliberadamente sencilla y no añade una dependencia de base vectorial. Para volúmenes muy grandes se podrá sustituir por una extensión vectorial de SQLite o un índice ANN sin cambiar la interfaz MCP.

## Tools MCP relacionadas

```text
embedding_status
calculate_embeddings
search_requirements_by_embedding
find_requirement_by_unique_identifier
find_requirement_by_identifier
get_local_requirement_by_id
get_local_requirement_by_absolute_number
```

`search_requirements_by_embedding` genera el vector de la consulta con el modelo configurado y solo compara contra requisitos activos cuyo vector corresponda al mismo modelo y al contenido actual.
