# doors-kb — MCP y base de conocimiento para IBM DOORS Classic

Capa de acceso para agentes de IA sobre **IBM Engineering Requirements Management DOORS Classic**.

El proyecto tiene dos mitades complementarias:

1. **MCP directo a DOORS** (`doors_kb.servers.doors_server`): un servidor MCP local, de **solo lectura**,
   que consulta DOORS mediante la interfaz Automation (COM/OLE) y DXL. Sirve para comprobar informacion
   actual, trazabilidad o un requisito concreto. Requiere Windows y DOORS instalado.
2. **Copia local en SQLite** (`doors_kb.db` + `doors_kb.sync`): una replica de consulta sincronizada de
   forma incremental, que desacopla las busquedas de IA de DOORS. Es multiplataforma y no necesita DOORS.

**DOORS es siempre la fuente de verdad.** La base local es un derivado reconstruible: nunca se modifican
requisitos desde aqui.

## Estado por hito

| Hito | Contenido | Estado |
|---|---|---|
| H0 | Conexion Automation, apertura explicita de modulo, lectura de requisitos | Implementado |
| H1 | MCP avanzado: validacion, busqueda, tipos, enlaces, multi-modulo, limites, timeouts | Implementado |
| H2 | Persistencia SQLite: modelo local, atributos, hashes, `sync_runs` | Implementado |
| H3 | Sincronizacion DOORS -> SQLite con cursor y borrado seguro | Implementado (pendiente validacion con DOORS real) |
| H4 | Indice lexical FTS5 mantenido de forma incremental | Implementado |
| H5 | Embeddings incrementales contra API compatible con OpenAI | Implementado (pendiente validacion con el endpoint real) |
| H6 | Busqueda hibrida por fusion de rankings (RRF) | Implementado |
| H7 | Servidor MCP sobre la copia local | Implementado |
| H8 | Graph-RAG: expansion de contexto por trazabilidad | Planificado |

## Instalacion

```bash
python -m venv .venv
# Linux/macOS
.venv/bin/pip install -e ".[dev,embeddings]"
# Windows (incluye pywin32 para el acceso COM a DOORS)
.\.venv\Scripts\python -m pip install -e ".[dev,win,embeddings]"
```

El extra `embeddings` solo hace falta para la busqueda semantica e hibrida; el nucleo
(sincronizacion, indice textual y servidor directo) funciona sin el.

## Uso rapido sin DOORS

El proyecto incluye una fuente falsa que reproduce la semantica de DOORS (paginacion por cursor,
truncado por atributo, altas/bajas). Permite entender y probar el sistema completo sin instalar nada:

```bash
python examples/demo_sync_fake.py        # ciclo de sincronizacion
python examples/demo_busqueda_hibrida.py # los tres modos de busqueda
```

## Sincronizacion contra DOORS real

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"
$env:DOORS_DXL_TIMEOUT_SECONDS  = "90"

doors-sync --module "/Proyecto/Requisitos/Requisitos del sistema" `
           --page-size 25 `
           --max-attribute-chars 20000 `
           --db .\doors_kb.sqlite3
```

El paso a paso completo, incluyendo el login manual de la sesion Automation y que hacer ante cada fallo
conocido, esta en [`docs/operacion_windows.md`](docs/operacion_windows.md).

## Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `DOORS_MODULE_PATH` | *(vacio)* | Modulo predeterminado; las tools MCP pueden sobreescribirlo |
| `DOORS_PROG_ID` | `DOORS.Application` | ProgID Automation de DOORS |
| `DOORS_START_TIMEOUT_SECONDS` | `30` | Timeout de creacion/inicio de la sesion Automation |
| `DOORS_DXL_TIMEOUT_SECONDS` | `90` | Timeout externo de Python para una llamada DXL |
| `DOORS_HARD_MAX_RESPONSE_CHARS` | `500000` | Limite duro del tamano serializado de las respuestas MCP |
| `DOORS_DXL_RUN_LIMIT_CYCLES` | `0` | Watchdog interno de DXL (`pragma runLim`); `0` lo desactiva |
| `DOORS_DB_PATH` | `./doors_kb.sqlite3` | Ruta de la copia local SQLite |
| `DOORS_SYNC_ATTRIBUTES` | `Object Heading,Object Text` | Atributos que entran en la sincronizacion y en el hash |
| `DOORS_SYNC_PAGE_SIZE` | `25` | Objetos por pagina DXL |
| `DOORS_MAX_ATTRIBUTE_CHARS` | `20000` | Truncado por atributo |
| `DOORS_SOURCE_LAST_MODIFIED_ATTRIBUTE` | *(vacio)* | Atributo del proyecto que se mapea a `source_last_modified` |
| `EMBEDDINGS_BASE_URL` | *(vacio)* | URL base del servicio de embeddings (protocolo OpenAI) |
| `EMBEDDINGS_API_KEY` | *(vacio)* | Clave de ese servicio. **Nunca se expone en las respuestas MCP** |
| `EMBEDDINGS_MODEL` | *(vacio)* | Nombre del modelo de embeddings |
| `EMBEDDINGS_ATTRIBUTES` | *(vacio)* | Atributos que entran en el texto a embeder. Solo pueden ser atributos ya sincronizados |
| `EMBEDDINGS_BATCH_SIZE` | `32` | Textos por peticion |
| `EMBEDDINGS_TIMEOUT_SECONDS` | `60` | Timeout de cada peticion al proveedor |

## Busqueda local

Una vez sincronizado el modulo, la copia local se consulta sin tocar DOORS:

```bash
doors-embed  --module "/Proyecto/Requisitos/Modulo" --db ./doors_kb.sqlite3
doors-search --module "/Proyecto/Requisitos/Modulo" --db ./doors_kb.sqlite3 \
             --mode hybrid "cuanto tarda en cerrarse la conexion"
```

| Modo | Para que sirve | Necesita embeddings |
|---|---|---|
| `lexical` | Identificadores, codigos y terminos exactos | No |
| `semantic` | Preguntas conceptuales y parafrasis | Si |
| `hybrid` | Cuando no sabes cual encaja mejor | Si |

`doors-embed` solo genera lo que falta: una segunda ejecucion sin cambios no hace ninguna
llamada al proveedor. Si el indice se corrompe, `doors-sync --rebuild-index` lo rehace desde
la copia local, sin volver a consultar DOORS.

## Configuracion MCP en VS Code

```json
{
  "servers": {
    "doors": {
      "type": "stdio",
      "command": "C:\\proyecto\\.venv\\Scripts\\python.exe",
      "args": ["-m", "doors_kb.servers.doors_server"],
      "env": {
        "DOORS_MODULE_PATH": "/Proyecto/Requisitos/Modulo",
        "DOORS_START_TIMEOUT_SECONDS": "30",
        "DOORS_DXL_TIMEOUT_SECONDS": "90",
        "DOORS_HARD_MAX_RESPONSE_CHARS": "500000"
      }
    },
    "doors-kb": {
      "type": "stdio",
      "command": "C:\\proyecto\\.venv\\Scripts\\python.exe",
      "args": ["-m", "doors_kb.servers.kb_server"],
      "env": {
        "DOORS_MODULE_PATH": "/Proyecto/Requisitos/Modulo",
        "DOORS_DB_PATH": "C:\\proyecto\\doors_kb.sqlite3",
        "EMBEDDINGS_BASE_URL": "https://mi-proveedor/v1",
        "EMBEDDINGS_MODEL": "nombre-del-modelo",
        "EMBEDDINGS_API_KEY": "..."
      }
    }
  }
}
```

Los dos servidores son complementarios: `doors-kb` es rapido y no necesita DOORS abierto,
pero responde con la ultima sincronizacion, asi que cada respuesta suya indica su frescura;
`doors` da el estado vivo. Lo habitual es buscar en el primero y verificar en el segundo.

## Documentacion

| Documento | Para que sirve |
|---|---|
| [`docs/especificacion.md`](docs/especificacion.md) | Especificacion de requisitos consolidada (fuente de RF/RNF) |
| [`docs/arquitectura.md`](docs/arquitectura.md) | Capas, responsabilidades y flujos internos |
| [`docs/adr.md`](docs/adr.md) | Decisiones de arquitectura y su motivacion |
| [`docs/trazabilidad.md`](docs/trazabilidad.md) | Que requisito cubre cada modulo y que test lo verifica |
| [`docs/guia_desarrollo.md`](docs/guia_desarrollo.md) | Montar el entorno, ejecutar tests, extender el sistema |
| [`docs/operacion_windows.md`](docs/operacion_windows.md) | Puesta en marcha con DOORS real y fallos conocidos |

## Desarrollo

```bash
.venv/bin/pytest -q          # toda la suite corre sin DOORS ni Windows
.venv/bin/ruff check src tests examples
```
