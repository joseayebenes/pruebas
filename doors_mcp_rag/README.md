# DOORS MCP + Requirements Knowledge Base

Prototipo para exponer IBM DOORS Classic a agentes de IA mediante MCP y construir una copia local de requisitos preparada para búsqueda, embeddings y RAG.

## Estado actual

Implementado en esta rama:

- acceso a IBM DOORS Classic mediante `DOORS.Application` + DXL;
- servidor MCP modular de solo lectura;
- validación estricta de atributos;
- timeouts del worker COM;
- persistencia local SQLite;
- sincronización DOORS → SQLite con detección `inserted / updated / unchanged`;
- detección segura de requisitos desaparecidos;
- reactivación de requisitos que reaparecen;
- paginación por cursor para evitar reescaneos crecientes;
- watchdog DXL configurable mediante `pragma runLim`;
- pruebas de sincronización con una fuente DOORS falsa;
- prueba de regresión del preámbulo DXL;
- especificación consolidada de requisitos y roadmap.

El prototipo MCP avanzado desarrollado anteriormente incluye además búsqueda directa y consulta de enlaces. La siguiente refactorización deberá integrar esas capacidades en el MCP modular de esta rama sin duplicar la capa COM/DXL.

## Próximas fases

1. Integrar búsqueda directa y trazabilidad en el MCP modular.
2. SQLite FTS5.
3. Embeddings incrementales.
4. Búsqueda híbrida lexical + semántica.
5. Tools MCP sobre la base local.
6. Graph-RAG usando los enlaces de DOORS.

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
│   └── sync_doors.py
├── tests/
│   ├── test_sync_fake.py
│   └── test_dxl_generation.py
└── docs/
    ├── SPECIFICATION.md
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

## Probar la lógica local

Desde `doors_mcp_rag`:

```powershell
python .\tests\test_sync_fake.py
```

La prueba no necesita DOORS. Valida altas, modificaciones, desapariciones y reapariciones de requisitos.

La prueba del preámbulo DXL necesita `pywin32` y debe ejecutarse en Windows:

```powershell
$env:PYTHONPATH = ".\sync"
python .\tests\test_dxl_generation.py
```

## Sincronización real

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"
$env:DOORS_DXL_TIMEOUT_SECONDS = "90"

python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --page-size 25 `
  --max-attribute-chars 20000
```

El flujo es:

```text
DOORS Classic
     │
     │ COM + DXL
     ▼
doors_client.py
     │
     │ páginas por cursor
     ▼
sync_service.py
     │
     ▼
repository.py
     │
     ▼
doors_requirements.db
```

DOORS continúa siendo la fuente de verdad. SQLite actúa como capa local optimizada para consultas de IA.

## MCP en VS Code

Configura `.vscode/mcp.json` usando el Python del entorno virtual:

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
        "DOORS_START_TIMEOUT_SECONDS": "30",
        "DOORS_DXL_TIMEOUT_SECONDS": "90",
        "DOORS_DXL_RUN_LIMIT_CYCLES": "0"
      }
    }
  }
}
```

Tools disponibles en el MCP modular actual:

```text
doors_configuration
start_doors_session
doors_status
list_object_attributes
validate_attributes
list_requirements
```

Consulta `docs/SPECIFICATION.md` para el catálogo completo de requisitos y el roadmap del proyecto.
