# Trazabilidad de requisitos DOORS -> SQLite -> MCP

## Objetivo

La copia local no debe contener solo el texto de los requisitos. También debe
conservar el grafo de relaciones estándar de IBM DOORS Classic para que el
agente pueda navegar dependencias, derivaciones y trazabilidad sin ejecutar DXL
en cada consulta.

```text
DOORS Classic
    │
    │ links source -> target
    ▼
traceability.py
    │
    ▼
SQLite.links
    │
    ▼
MCP
```

## Modelo almacenado

Cada enlace se normaliza como una arista dirigida:

```text
source_module_path
source_absolute_number
       │
       │ link_module_path
       ▼
target_module_path
target_absolute_number
```

La tabla `links` ya formaba parte del diseño inicial. La capa de trazabilidad
añade `synced_at` mediante migración automática a bases existentes.

La clave lógica del enlace es:

```text
(
  source_module_path,
  source_absolute_number,
  target_module_path,
  target_absolute_number,
  link_module_path
)
```

No se duplica `REM_UniqueIdentifier` en la tabla de links. Al consultar, los
extremos se enriquecen mediante `LEFT JOIN` contra `requirements`, por lo que el
MCP devuelve cuando están disponibles localmente:

- `database_id`
- `identifier`
- `unique_identifier`
- `outline_number`
- `heading`

Si el requisito relacionado pertenece a un módulo todavía no descargado, se
conservan igualmente `module_path` y `absolute_number` y
`available_locally=false`.

## Extracción desde DOORS

`DoorsTraceabilitySource` usa los enlaces estándar DXL:

```dxl
obj -> "*"    // outgoing
obj <- "*"    // incoming
```

Los enlaces se transforman siempre a `source -> target`, independientemente de
desde qué extremo se hayan encontrado.

Para los entrantes, DOORS puede necesitar cargar los módulos origen. Por defecto
la sincronización es conservadora: si algún módulo origen no se puede cargar,
se aborta la actualización de trazabilidad y se mantiene la copia SQLite
anterior.

Los enlaces externos OSLC no se incluyen en esta fase.

## Seguridad ante sincronizaciones incompletas

La extracción completa se hace primero en memoria. La tabla `links` solo se
sustituye al terminar correctamente.

```text
leer links DOORS
      │
      ├── error -> conservar SQLite anterior
      │
      └── OK -> transacción DELETE + INSERT
```

Los modos de refresco son independientes:

- `both`: reemplaza todos los enlaces que tocan el módulo.
- `outgoing`: reemplaza solo enlaces cuyo origen es el módulo.
- `incoming`: reemplaza solo enlaces cuyo destino es el módulo.

Esto permite usar `outgoing` cuando no se desea cargar módulos externos.

## Sincronización junto a los requisitos

```powershell
python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/System" `
  --sync-links `
  --links-direction both
```

Si se desea una extracción más rápida que no cargue módulos origen:

```powershell
python .\sync\sync_doors.py `
  --module "/Proyecto/Requisitos/System" `
  --sync-links `
  --links-direction outgoing
```

## Sincronización independiente

También se puede actualizar solo el grafo sin volver a descargar los requisitos:

```powershell
python .\sync\sync_traceability.py `
  --module "/Proyecto/Requisitos/System" `
  --direction both
```

Los requisitos del módulo deben existir previamente en SQLite porque sus
`Absolute Number` son la lista de objetos que se recorre.

## Tools MCP

### Estado

```text
traceability_status
```

Devuelve número de enlaces y últimas sincronizaciones del módulo.

### Actualizar desde DOORS

```text
sync_traceability
```

Requiere una sesión Automation autenticada y actualiza SQLite.

### Navegar relaciones

```text
get_requirement_relations_by_absolute_number
get_requirement_relations_by_unique_identifier
get_requirement_relations_by_identifier
get_requirement_relations_by_id
```

Todas aceptan:

```text
direction = incoming | outgoing | both
```

Una relación devuelta contiene:

```json
{
  "direction": "outgoing",
  "link_module_path": "/Links/Satisfies",
  "source": {
    "module_path": "/Project/System",
    "absolute_number": 10,
    "unique_identifier": "REQ_MENSAJES"
  },
  "target": {
    "module_path": "/Project/Software",
    "absolute_number": 51,
    "unique_identifier": "SWR_MENSAJES"
  },
  "related_requirement": {
    "unique_identifier": "SWR_MENSAJES"
  }
}
```

## Futuro Graph-RAG

Esta tabla es la base para expandir los resultados semánticos con vecinos del
grafo:

```text
embedding search
      │
      ▼
 top requisitos
      │
      ▼
 incoming/outgoing links
      │
      ▼
 contexto de requisitos relacionados
```

El siguiente paso podrá ponderar por profundidad, tipo de link module y sentido
de la relación, sin modificar el formato de las tools MCP actuales.
