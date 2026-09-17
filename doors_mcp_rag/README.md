# Requirements Knowledge Base MCP

Proyecto para copiar requisitos y trazabilidad desde IBM DOORS Classic a SQLite y exponer **solo la base de datos local** a un agente mediante MCP.

La arquitectura separa completamente la ingesta del uso por IA:

```text
                    PROCESO DE INGESTA
IBM DOORS Classic ───────────────────────> SQLite
          │                                  │
          │ COM + DXL                        │ requirements
          │                                  │ attributes
          └─────────────────────────────────>│ links
                                             │ embeddings
                                             │
                                             ▼
                                      MCP READ-ONLY
                                             │
                                             ▼
                                      VS Code / agente
```

## Regla principal del MCP

El servidor `mcp/doors_mcp.py` **no accede a DOORS**.

No importa `DoorsClient`, no usa COM, no ejecuta DXL, no inicia sesiones de DOORS y no contiene tools de sincronizacion. El unico argumento de configuracion del proceso MCP es:

```text
--db <ruta-a-la-base-sqlite>
```

SQLite se abre con `mode=ro` y `PRAGMA query_only=ON`, por lo que el MCP tampoco puede modificar la base accidentalmente.

La unica llamada externa opcional durante una busqueda semantica es `Daisei.create_embedding()` para convertir **el texto de la consulta** en un vector. Los requisitos, atributos, identificadores y relaciones devueltos proceden siempre de SQLite. No se usa `chat`, `chat_stream`, `list_models` ni ninguna llamada de generacion de texto.

## Datos almacenados

La identidad estable de un objeto DOORS es:

```text
(module_path, absolute_number)
```

La tabla `requirements` contiene, entre otros:

```text
id                       ID interno SQLite
module_path              modulo DOORS de origen
absolute_number          Absolute Number
identifier               identifier(obj)
unique_identifier        REM_UniqueIdentifier; nullable
outline_number
heading
text
content_hash
embedding                BLOB float32; nullable
embedding_model
embedding_dimensions
embedding_content_hash
embedding_updated_at
```

Los atributos adicionales se guardan en `requirement_attributes` y las relaciones de trazabilidad en `links`:

```text
source_module_path + source_absolute_number
                  │
                  │ link_module_path
                  ▼
target_module_path + target_absolute_number
```

## Flujo recomendado

### 1. Descargar/actualizar la base

Este paso es independiente del MCP y se ejecuta cuando quieras refrescar la copia local:

```powershell
python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --sync-links `
  --links-direction both
```

`REM_UniqueIdentifier` se descarga automaticamente.

### 2. Calcular embeddings de los requisitos

La configuracion de red y API esta encapsulada por tu clase `Daisei` y `ConnectionConfig.from_env()`.

El adaptador del proyecto esta en:

```text
sync/daisei_embedding.py
```

Solo utiliza:

```python
llm.create_embedding(text, model)
```

La configuracion se carga desde `.env` en la raiz del proyecto. El modelo de embedding se controla con:

```text
DAISEI_EMBEDDING_MODEL=text-embedding-gte-multilingual-base
```

El resto de parametros (`base_url`, `api_key`, proxy, certificados, timeout, etc.) los resuelve `ConnectionConfig.from_env()` segun la implementacion de Daisei.

Para calcular o actualizar los vectores despues de descargar DOORS:

```powershell
python .\sync\embed_requirements.py `
  --db .\sync\doors_requirements.db
```

El calculo es incremental: un requisito solo se vuelve a vectorizar si no tiene embedding, cambia el modelo o cambia su `content_hash`.

### 3. Ejecutar solo el MCP

```powershell
python .\mcp\doors_mcp.py `
  --db "C:\ruta\doors_requirements.db"
```

No necesita una sesion DOORS abierta.

## Configuracion en VS Code

El MCP solo recibe el path de la base:

```json
{
  "servers": {
    "requirements": {
      "type": "stdio",
      "command": "C:\\ruta\\doors_mcp_rag\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\ruta\\doors_mcp_rag\\mcp\\doors_mcp.py",
        "--db",
        "C:\\ruta\\doors_requirements.db"
      ]
    }
  }
}
```

La configuracion de Daisei no se repite en `mcp.json`; queda encapsulada en Python + `.env`.

## Tools MCP locales

El servidor expone solo consultas a SQLite:

```text
database_status
list_modules
find_requirement_by_unique_identifier
find_requirement_by_identifier
get_requirement_by_id
get_requirement_by_absolute_number
search_requirements_text
search_requirements_by_embedding
get_relations_by_unique_identifier
get_relations_by_identifier
get_relations_by_id
get_relations_by_absolute_number
```

Ejemplos conceptuales:

```text
find_requirement_by_unique_identifier("REQ_MENSAJES")
get_requirement_by_id(123)
get_requirement_by_absolute_number(451, module_path="/Proyecto/Requisitos")
search_requirements_text("timeout de comunicaciones")
search_requirements_by_embedding("perdida del enlace de datos", limit=10)
get_relations_by_unique_identifier("REQ_MENSAJES", direction="both")
```

Las consultas de relaciones enriquecen `source` y `target` con los datos locales del requisito cuando el extremo relacionado tambien esta presente en `requirements`.

## Estructura relevante

```text
doors_mcp_rag/
├── .env                         # configuracion Daisei; no se pasa al MCP
├── mcp/
│   └── doors_mcp.py             # SOLO SQLite + embedding de consulta
├── sync/
│   ├── local_repository.py      # acceso read-only usado por MCP
│   ├── daisei_embedding.py      # adaptador de create_embedding
│   ├── repository.py            # repositorio de ingesta/escritura
│   ├── embeddings.py            # generacion incremental
│   ├── embed_requirements.py
│   ├── doors_client.py          # solo proceso de ingesta
│   ├── sync_service.py
│   ├── traceability.py
│   ├── sync_traceability.py
│   └── sync_doors.py
└── tests/
```

La separacion es deliberada: **DOORS alimenta la base; el agente solo ve la base**.
