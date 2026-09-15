# Especificación técnica — DOORS MCP + Requirements Knowledge Base

## 1. Objetivo

Construir una capa de integración entre **IBM DOORS Classic** y agentes de IA que permita consultar requisitos de forma segura mediante MCP y, en paralelo, mantener una copia local en SQLite preparada para búsqueda textual, embeddings, RAG y análisis de trazabilidad.

DOORS seguirá siendo la **fuente de verdad**. La base local será una representación optimizada para lectura y análisis.

## 2. Arquitectura objetivo

```text
Agente IA / VS Code
        │
        │ MCP
        ▼
Servidor MCP Python
        │
        ├──────────────► DOORS Classic
        │                  │
        │                  │ COM + DXL
        │                  ▼
        │             Requisitos / Links
        │
        ▼
SQLite local
 ├── modules
 ├── requirements
 ├── requirement_attributes
 ├── links
 ├── sync_runs
 ├── FTS5                   [planificado]
 └── embeddings             [planificado]
        │
        ▼
Búsqueda híbrida / RAG      [planificado]
```

## 3. Decisiones principales

- Windows es obligatorio para la capa COM de DOORS Classic.
- El servidor crea su propia instancia `DOORS.Application`.
- Los módulos se abren en lectura.
- No se expone una tool genérica `run_dxl`.
- Todas las llamadas COM se serializan en un único hilo.
- Las esperas del worker tienen timeout externo en Python.
- El watchdog interno DXL es configurable mediante `DOORS_DXL_RUN_LIMIT_CYCLES`.
- La sincronización usa paginación por cursor, no offset creciente.
- Un requisito se identifica por `(module_path, absolute_number)`.
- Los cambios se detectan por hash SHA-256 del contenido relevante.
- Los requisitos ausentes solo se marcan eliminados al completar una sincronización entera correctamente.

## 4. Requisitos funcionales

| ID | Requisito | Prioridad | Estado |
|---|---|---|---|
| RF-001 | El sistema deberá conectarse a IBM DOORS Classic mediante Automation COM. | Alta | Implementado |
| RF-002 | El sistema deberá crear y mantener una única sesión `DOORS.Application` por proceso. | Alta | Implementado |
| RF-003 | El sistema deberá ejecutar las operaciones COM desde un único hilo/apartamento COM. | Alta | Implementado |
| RF-004 | El sistema deberá permitir iniciar explícitamente la sesión Automation. | Alta | Implementado |
| RF-005 | El usuario deberá poder autenticarse manualmente en la ventana Automation. | Alta | Implementado |
| RF-006 | El sistema deberá abrir un módulo formal por su ruta interna completa. | Alta | Implementado |
| RF-007 | El módulo deberá abrirse en modo lectura. | Alta | Implementado |
| RF-008 | La ruta del módulo podrá obtenerse de `DOORS_MODULE_PATH`. | Media | Implementado |
| RF-009 | El MCP deberá poder aceptar `module_path` cuando no se quiera usar el valor por defecto. | Media | Implementado en MCP modular |
| RF-010 | El sistema deberá comprobar el estado de la sesión y del módulo. | Alta | Implementado |
| RF-011 | El sistema deberá listar los atributos de objeto del módulo. | Alta | Implementado |
| RF-012 | El sistema deberá validar los nombres de atributos antes de consultar valores. | Alta | Implementado |
| RF-013 | Ante un atributo inválido, el sistema deberá devolver un error explícito en lugar de un string vacío silencioso. | Alta | Implementado |
| RF-014 | La validación deberá sugerir nombres similares cuando sea posible. | Media | Implementado en cliente avanzado |
| RF-015 | El MCP deberá obtener requisitos paginados. | Alta | Implementado |
| RF-016 | Los requisitos deberán incluir `Absolute Number`. | Alta | Implementado |
| RF-017 | Los requisitos deberán incluir identificador y número de esquema cuando estén disponibles. | Alta | Implementado |
| RF-018 | Por defecto deberán consultarse `Object Heading` y `Object Text`. | Alta | Implementado |
| RF-019 | El usuario deberá poder solicitar atributos adicionales. | Alta | Implementado |
| RF-020 | Deberá existir un máximo configurable de caracteres por atributo. | Media | Implementado |
| RF-021 | Los objetos eliminados deberán excluirse por defecto de la sincronización. | Alta | Implementado |
| RF-022 | Las filas, celdas y cabeceras de tablas nativas deberán excluirse por defecto de la sincronización RAG. | Media | Implementado |
| RF-023 | La paginación de sincronización deberá continuar desde el último objeto visitado. | Alta | Implementado |
| RF-024 | El cursor de paginación deberá basarse en `Absolute Number`. | Alta | Implementado |
| RF-025 | El sistema deberá detectar cursores repetidos para evitar bucles infinitos. | Alta | Implementado |
| RF-026 | El sistema deberá convertir la salida DXL a JSON estructurado. | Alta | Implementado |
| RF-027 | La interfaz MCP deberá utilizar transporte stdio. | Alta | Implementado |
| RF-028 | Los logs del MCP deberán escribirse en stderr y no contaminar stdout. | Alta | Implementado en prototipo avanzado |
| RF-029 | El MCP no deberá ofrecer ejecución DXL arbitraria al agente. | Alta | Implementado |
| RF-030 | El sistema deberá poder buscar requisitos por término textual. | Alta | Implementado en prototipo avanzado / pendiente de integrar en MCP modular |
| RF-031 | La búsqueda deberá permitir seleccionar un atributo concreto. | Media | Implementado en prototipo avanzado |
| RF-032 | La búsqueda deberá soportar coincidencia literal. | Alta | Implementado en prototipo avanzado |
| RF-033 | La búsqueda podrá soportar expresiones regulares DXL. | Media | Implementado en prototipo avanzado |
| RF-034 | El sistema deberá poder obtener enlaces salientes de un requisito. | Alta | Implementado en prototipo avanzado |
| RF-035 | El sistema deberá poder obtener enlaces entrantes de un requisito. | Alta | Implementado en prototipo avanzado |
| RF-036 | El sistema deberá poder cargar módulos origen en lectura para completar inlinks. | Media | Implementado en prototipo avanzado |
| RF-037 | Los enlaces externos OSLC deberán identificarse como no incluidos cuando no se soporten. | Media | Implementado en prototipo avanzado |
| RF-038 | La capa local deberá utilizar SQLite. | Alta | Implementado |
| RF-039 | SQLite deberá almacenar módulos sincronizados. | Alta | Implementado |
| RF-040 | SQLite deberá almacenar requisitos. | Alta | Implementado |
| RF-041 | SQLite deberá almacenar atributos personalizados en una tabla flexible nombre/valor. | Alta | Implementado |
| RF-042 | SQLite deberá incluir una tabla preparada para enlaces. | Alta | Implementado |
| RF-043 | SQLite deberá registrar ejecuciones de sincronización. | Alta | Implementado |
| RF-044 | Cada requisito deberá identificarse de forma única por módulo y `Absolute Number`. | Alta | Implementado |
| RF-045 | El sistema deberá calcular un hash de contenido de cada requisito. | Alta | Implementado |
| RF-046 | Una primera sincronización deberá clasificar requisitos como `inserted`. | Alta | Implementado |
| RF-047 | Un requisito modificado deberá clasificarse como `updated`. | Alta | Implementado |
| RF-048 | Un requisito sin cambios deberá clasificarse como `unchanged`. | Alta | Implementado |
| RF-049 | Un requisito que reaparece deberá reactivarse aunque su contenido coincida con el hash anterior. | Alta | Implementado |
| RF-050 | Los requisitos ausentes deberán marcarse como eliminados solo tras una sincronización completa exitosa. | Alta | Implementado |
| RF-051 | Una sincronización parcial fallida no deberá provocar falsos borrados. | Alta | Implementado |
| RF-052 | El historial deberá registrar vistos, insertados, actualizados, sin cambios y eliminados. | Alta | Implementado |
| RF-053 | El sincronizador deberá aceptar atributos adicionales desde CLI. | Media | Implementado |
| RF-054 | El sincronizador deberá aceptar un atributo opcional de última modificación. | Media | Implementado |
| RF-055 | La base local deberá poder consultarse sin mantener DOORS abierto. | Alta | Implementado a nivel de repositorio |
| RF-056 | Se deberá añadir búsqueda textual local con SQLite FTS5. | Alta | Planificado |
| RF-057 | Se deberán generar embeddings para los requisitos. | Alta | Planificado |
| RF-058 | Los embeddings deberán regenerarse solo para elementos insertados o modificados. | Alta | Planificado |
| RF-059 | Deberá almacenarse el modelo/versionado utilizado para cada embedding. | Media | Planificado |
| RF-060 | El sistema deberá ofrecer búsqueda semántica top-k. | Alta | Planificado |
| RF-061 | El sistema deberá ofrecer búsqueda híbrida lexical + vectorial. | Alta | Planificado |
| RF-062 | La búsqueda híbrida deberá permitir filtros estructurados por atributos. | Alta | Planificado |
| RF-063 | El MCP deberá exponer consultas sobre la copia local. | Alta | Planificado |
| RF-064 | El agente deberá poder consultar cuándo se realizó la última sincronización. | Media | Planificado |
| RF-065 | La capa RAG deberá poder expandir contexto mediante links de trazabilidad. | Alta | Planificado |
| RF-066 | El sistema deberá soportar varios módulos en la misma base de conocimiento. | Alta | Parcial |

## 5. Requisitos no funcionales

| ID | Requisito | Prioridad | Estado |
|---|---|---|---|
| RNF-001 | Las operaciones sobre DOORS deberán ser de solo lectura salvo el acto de crear la sesión Automation. | Alta | Implementado |
| RNF-002 | Las llamadas COM no deberán ejecutarse concurrentemente sobre la misma sesión. | Alta | Implementado |
| RNF-003 | `start_session()` deberá tener timeout configurable. | Alta | Implementado |
| RNF-004 | Las ejecuciones DXL deberán tener timeout externo configurable. | Alta | Implementado |
| RNF-005 | Tras un timeout, el worker deberá considerarse potencialmente bloqueado y requerir reinicio. | Alta | Implementado |
| RNF-006 | Los errores COM deberán convertirse en mensajes comprensibles. | Alta | Implementado |
| RNF-007 | El sistema deberá reintentar errores COM temporales de servidor ocupado. | Media | Implementado |
| RNF-008 | El watchdog interno DXL deberá poder configurarse sin mostrar diálogos modales durante sincronizaciones controladas. | Alta | Implementado |
| RNF-009 | El código DXL generado deberá utilizar saltos de línea reales y disponer de prueba de regresión. | Alta | Implementado |
| RNF-010 | Las consultas deberán limitar el tamaño de cada atributo. | Alta | Implementado |
| RNF-011 | El MCP avanzado deberá limitar el tamaño total de sus respuestas JSON. | Alta | Implementado en prototipo avanzado |
| RNF-012 | Las consultas grandes deberán paginarse. | Alta | Implementado |
| RNF-013 | La sincronización deberá tener complejidad aproximadamente lineal respecto al número de objetos recorridos. | Alta | Implementado mediante cursor |
| RNF-014 | El esquema SQLite deberá ser independiente de los atributos específicos de cada proyecto. | Alta | Implementado |
| RNF-015 | La lógica de sincronización deberá poder probarse sin una instalación real de DOORS. | Alta | Implementado conceptualmente; test fake pendiente de incorporar a esta rama |
| RNF-016 | El sistema deberá preservar trazabilidad de errores y estadísticas de cada sincronización. | Media | Implementado |
| RNF-017 | La arquitectura deberá separar cliente DOORS, sincronización, persistencia y servidor MCP. | Alta | Implementado |

## 6. Modelo de datos SQLite

### `modules`

Clave primaria: `module_path`.

Mantiene la última sincronización y la última sincronización completa.

### `requirements`

Clave lógica única:

```text
(module_path, absolute_number)
```

Campos principales:

- identifier
- outline_number
- heading
- text
- is_deleted
- source_last_modified
- content_hash
- synced_at

### `requirement_attributes`

Modelo flexible:

```text
requirement_id | name | value_text
```

### `links`

Preparada para:

```text
source_module_path
source_absolute_number
target_module_path
target_absolute_number
link_module_path
```

### `sync_runs`

Registra el resultado de cada sincronización y sus estadísticas.

## 7. Algoritmo de sincronización

```text
1. Validar atributos
2. Crear sync_run(status=running)
3. cursor = null
4. Solicitar una página a DOORS
5. Convertir cada objeto a RequirementRecord
6. Calcular hash
7. insert / update / unchanged
8. Guardar Absolute Number en seen
9. Continuar con next_after_absolute_number
10. Si todas las páginas terminan correctamente:
      marcar ausentes como eliminados
      sync_run = success
11. Si cualquier página falla:
      sync_run = failed
      NO marcar ausentes como eliminados
```

## 8. Gestión de timeouts

Existen dos mecanismos distintos.

### Timeout externo Python

```text
DOORS_START_TIMEOUT_SECONDS = 30
DOORS_DXL_TIMEOUT_SECONDS   = 90
```

Se aplica mediante `Future.result(timeout=N)`.

### Watchdog interno DXL

```text
DOORS_DXL_RUN_LIMIT_CYCLES = 0
```

El DXL generado comienza con:

```dxl
pragma runLim, 0
```

El valor 0 se utiliza para evitar que una sincronización controlada quede bloqueada por el diálogo modal `DXL Execution Timeout`. El timeout externo de Python continúa activo.

## 9. Seguridad

No se debe añadir una tool del tipo:

```python
run_dxl(code: str)
```

porque convertiría al agente en un ejecutor DXL arbitrario con capacidad potencial para modificar la base de requisitos.

Las tools deberán representar operaciones concretas, validadas y de lectura.

## 10. Roadmap

### Fase 1 — Persistencia local

Estado: completada.

### Fase 2 — Sincronización DOORS → SQLite

Estado: implementada, pendiente de validación final en el módulo real.

### Fase 3 — SQLite FTS5

Objetivos:

- búsqueda lexical local;
- identificadores exactos;
- ranking BM25;
- snippets.

### Fase 4 — Embeddings

Objetivos:

- generar embedding de heading + text + atributos seleccionados;
- recalcular únicamente requisitos nuevos/modificados;
- guardar `content_hash`, modelo y versión.

### Fase 5 — Búsqueda híbrida

Combinar:

```text
FTS5/BM25 + similitud vectorial + filtros estructurados
```

### Fase 6 — MCP sobre Knowledge Base

Tools previstas:

```text
sync_module
sync_status
db_get_requirement
text_search_requirements
semantic_search_requirements
hybrid_search_requirements
db_get_links
database_stats
```

### Fase 7 — Graph-RAG

Expandir resultados semánticos mediante enlaces entrantes/salientes para análisis de impacto y cobertura.
