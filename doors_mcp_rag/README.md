# DOORS MCP + Requirements Knowledge Base

Prototipo para exponer IBM DOORS Classic a agentes de IA mediante MCP y construir una copia local de requisitos preparada para búsqueda, embeddings y RAG.

## Estado actual

Implementado:

- acceso a IBM DOORS Classic mediante `DOORS.Application` + DXL;
- servidor MCP de solo lectura;
- validación estricta de atributos;
- búsqueda literal/regex y consulta de enlaces de trazabilidad;
- soporte para varios módulos mediante `module_path`;
- timeouts del worker COM y límites de respuesta;
- persistencia local SQLite;
- sincronización DOORS → SQLite con detección `inserted / updated / unchanged`;
- detección segura de requisitos desaparecidos;
- paginación por cursor para evitar reescaneos crecientes;
- corrección del watchdog DXL mediante `pragma runLim` configurable.

Planificado:

1. SQLite FTS5.
2. Embeddings incrementales.
3. Búsqueda híbrida lexical + semántica.
4. Tools MCP sobre la base local.
5. Graph-RAG usando los enlaces de DOORS.

## Estructura

```text
mcp/
  doors_mcp_advanced.py
sync/
  models.py
  repository.py
  doors_client.py
  sync_service.py
  sync_doors.py
tests/
  test_dxl_generation.py
docs/
  README_STEP1.md
  README_STEP2.md
  README_TIMEOUT_FIX.md
  README_DXL_PARSE_FIX.md
  Especificacion_Proyecto_DOORS_MCP_RAG.docx
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

## Sincronización

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"
$env:DOORS_DXL_TIMEOUT_SECONDS = "90"

python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --page-size 25 `
  --max-attribute-chars 20000
```

DOORS continúa siendo la fuente de verdad. SQLite actúa como capa local optimizada para consultas de IA.
