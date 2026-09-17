# DOORS MCP + Requirements Knowledge Base

Prototipo para exponer IBM DOORS Classic a agentes de IA mediante MCP y construir una copia local de requisitos preparada para búsqueda, embeddings, trazabilidad y RAG.

## Estado actual

Implementado en esta rama:

- acceso a IBM DOORS Classic mediante `DOORS.Application` + DXL;
- servidor MCP modular;
- validación estricta de atributos;
- timeouts del worker COM;
- persistencia local SQLite y migraciones de esquema;
- sincronización DOORS → SQLite con detección `inserted / updated / unchanged`;
- `REM_UniqueIdentifier` proyectado a `requirements.unique_identifier` (`NULL` si está vacío);
- columna `embedding` en SQLite, almacenada como vector `float32` en BLOB;
- metadatos de embedding: modelo, dimensión, hash del contenido y fecha;
- invalidación automática del embedding cuando cambia el requisito;
- generación incremental de embeddings mediante un endpoint OpenAI-compatible configurable;
- búsqueda semántica local por similitud coseno;
- búsquedas MCP por UniqueIdentifier, identifier de DOORS, Absolute Number e ID SQLite;
- extracción de enlaces entrantes y salientes estándar de DOORS;
- persistencia del grafo de trazabilidad en `links`;
- consulta MCP de relaciones por UniqueIdentifier, identifier, Absolute Number e ID SQLite;
- detección segura de requisitos desaparecidos;
- reactivación de requisitos que reaparecen;
- paginación por cursor para evitar reescaneos crecientes;
- watchdog DXL configurable mediante `pragma runLim`;
- saneamiento de caracteres de control en JSON procedente de DOORS;
- pruebas locales de sincronización, embeddings y trazabilidad.

## Estructura

```text
doors_mcp_rag/
├── README.md
├── requirements.txt
├── mcp/
│   └── doors_mcp.py
├── sync/
│   ├── models.py
│   ├── repository.py
│   ├── doors_client.py
│   ├── sync_service.py
│   ├── traceability.py
│   ├── sync_traceability.py
│   ├── embeddings.py
│   ├── embed_requirements.py
│   └── sync_doors.py
├── tests/
│   ├── test_sync_fake.py
│   ├── test_repository_embeddings.py
│   ├── test_traceability_repository.py
│   ├── test_dxl_generation.py
│   └── test_json_control_chars.py
└── docs/
    ├── SPECIFICATION.md
    ├── EMBEDDINGS.md
    ├── TRACEABILITY.md
    ├── README_STEP1.md
    ├── README_TIMEOUT_FIX.md
    └── README_DXL_PARSE_FIX.md
```

## Requisitos

- Windows
- IBM DOORS Classic
- Python 3.10+

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Modelo SQLite

La identidad estable del objeto DOORS sigue siendo:

```text
(module_path, absolute_number)
```

Además se almacenan:

```text
identifier            identifier(obj) de DOORS
unique_identifier     REM_UniqueIdentifier; puede ser NULL
embedding             BLOB float32; puede ser NULL
embedding_model       modelo que generó el vector
embedding_dimensions  número de componentes
embedding_content_hash
embedding_updated_at
```

`unique_identifier` tiene índice para búsquedas rápidas, pero no se fuerza `UNIQUE` a nivel SQL porque distintos módulos podrían reutilizar el mismo valor.

La trazabilidad se guarda en `links`:

```text
source_module_path
source_absolute_number
       │
       │ link_module_path
       ▼
target_module_path
target_absolute_number
synced_at
```

Los extremos se enriquecen en las consultas mediante `LEFT JOIN` con `requirements`, por lo que el MCP devuelve `identifier`, `unique_identifier`, heading e ID local cuando el requisito relacionado también está descargado.

## Sincronización real

`REM_UniqueIdentifier` se añade automáticamente a los atributos descargados, por lo que no hay que indicarlo en `--attributes`.

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"
$env:DOORS_DXL_TIMEOUT_SECONDS = "90"

python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --page-size 25 `
  --max-attribute-chars 20000
```

DOORS continúa siendo la fuente de verdad. SQLite actúa como capa local optimizada para consultas de IA.

## Sincronizar relaciones de trazabilidad

La descarga de requisitos puede incluir también los enlaces:

```powershell
python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --sync-links `
  --links-direction both
```

También se pueden refrescar únicamente las relaciones, sin volver a descargar los requisitos:

```powershell
python .\sync\sync_traceability.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --direction both
```

Para relaciones entrantes, DOORS puede necesitar cargar los módulos origen. Por defecto, si alguno no se puede cargar, la sincronización de links se aborta antes de modificar SQLite para conservar la copia anterior completa.

Si solo interesan enlaces cuyo origen está en el módulo actual:

```powershell
python .\sync\sync_traceability.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --outgoing-only
```

Los enlaces externos OSLC no se incluyen en esta fase.

## Configurar embeddings

El código usa el SDK de OpenAI, pero el endpoint y el modelo no están fijados. Configura tu servidor OpenAI-compatible mediante variables de entorno:

```powershell
$env:DOORS_EMBEDDING_BASE_URL = "http://TU_SERVIDOR:PUERTO/v1"
$env:DOORS_EMBEDDING_MODEL = "TU_MODELO_DE_EMBEDDINGS"
$env:DOORS_EMBEDDING_API_KEY = "opcional-si-tu-servidor-la-necesita"
$env:DOORS_EMBEDDING_BATCH_SIZE = "32"
$env:DOORS_EMBEDDING_TIMEOUT_SECONDS = "60"
$env:DOORS_EMBEDDING_MAX_INPUT_CHARS = "30000"
```

La clave puede omitirse si el servidor local no usa autenticación.

### Opción A: sincronizar y después calcular embeddings

```powershell
python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --calculate-embeddings
```

Se pueden combinar requisitos, relaciones y embeddings:

```powershell
python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --sync-links `
  --calculate-embeddings
```

### Opción B: calcularlos posteriormente sin abrir DOORS

```powershell
python .\sync\embed_requirements.py `
  --db .\sync\doors_requirements.db `
  --module "/Proyecto/Requisitos/Requisitos del sistema"
```

Por defecto solo se calculan vectores ausentes, de otro modelo o cuyo requisito haya cambiado. Para regenerarlos todos:

```powershell
python .\sync\embed_requirements.py --force
```

## Texto enviado al modelo de embeddings

Cada vector representa una composición determinista de:

```text
Module
UniqueIdentifier
DOORS Identifier
Outline
Heading
Text
atributos adicionales de DOORS
```

No se envía el BLOB almacenado ni metadatos técnicos de sincronización.

## MCP en VS Code

Ejemplo `.vscode/mcp.json`:

```json
{
  "servers": {
    "doors": {
      "type": "stdio",
      "command": "C:\\ruta\\doors_mcp_rag\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\ruta\\doors_mcp_rag\\mcp\\doors_mcp.py"
      ],
      "env": {
        "DOORS_MODULE_PATH": "/Proyecto/Requisitos/Requisitos del sistema",
        "DOORS_SQLITE_PATH": "C:\\ruta\\doors_mcp_rag\\sync\\doors_requirements.db",
        "DOORS_START_TIMEOUT_SECONDS": "30",
        "DOORS_DXL_TIMEOUT_SECONDS": "90",
        "DOORS_DXL_RUN_LIMIT_CYCLES": "0",
        "DOORS_EMBEDDING_BASE_URL": "http://TU_SERVIDOR:PUERTO/v1",
        "DOORS_EMBEDDING_MODEL": "TU_MODELO_DE_EMBEDDINGS",
        "DOORS_EMBEDDING_API_KEY": ""
      }
    }
  }
}
```

## Tools MCP

Acceso directo a DOORS:

```text
doors_configuration
start_doors_session
doors_status
list_object_attributes
validate_attributes
list_requirements
```

Acceso a la copia local SQLite:

```text
local_database_status
find_requirement_by_unique_identifier
find_requirement_by_identifier
get_local_requirement_by_id
get_local_requirement_by_absolute_number
```

Trazabilidad:

```text
traceability_status
sync_traceability
get_requirement_relations_by_absolute_number
get_requirement_relations_by_unique_identifier
get_requirement_relations_by_identifier
get_requirement_relations_by_id
```

Embeddings:

```text
embedding_status
calculate_embeddings
search_requirements_by_embedding
```

Ejemplos conceptuales:

```text
find_requirement_by_unique_identifier("REQ_MENSAJES")
get_local_requirement_by_absolute_number(123, module_path="/Proyecto/Requisitos")
get_requirement_relations_by_unique_identifier("REQ_MENSAJES", direction="both")
search_requirements_by_embedding("requisitos sobre pérdida de comunicaciones", limit=10)
```

Una relación MCP devuelve los extremos `source` y `target`, la dirección vista desde el requisito consultado, el `link_module_path` y un bloque `related_requirement` para que el agente pueda navegar el grafo directamente.

La búsqueda semántica genera el embedding de la consulta con el mismo modelo configurado y calcula similitud coseno contra los vectores válidos almacenados en SQLite.

## Pruebas

```powershell
python .\tests\test_sync_fake.py
python .\tests\test_repository_embeddings.py
python .\tests\test_traceability_repository.py
```

Estas pruebas no necesitan un endpoint real de embeddings ni una sesión DOORS. La parte DXL real de trazabilidad debe validarse contra vuestro entorno DOORS Classic.

Consulta `docs/SPECIFICATION.md` para el catálogo general de requisitos, `docs/EMBEDDINGS.md` para la capa vectorial y `docs/TRACEABILITY.md` para el grafo de relaciones.
