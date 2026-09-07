# Guia de desarrollo

## Montar el entorno

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Linux/macOS: nucleo + herramientas
```

En la maquina con DOORS, ademas del nucleo hace falta el extra de Windows:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev,win]"
```

## Ejecutar las comprobaciones

```bash
.venv/bin/pytest -q                       # toda la suite: no necesita DOORS ni Windows
.venv/bin/ruff check src tests examples   # estilo e imports
.venv/bin/python examples/demo_sync_fake.py   # recorrido narrado del sistema
```

Que la suite completa corra sin DOORS no es casualidad: es el requisito RNF-013 y la razon
de que exista `FakeDoorsSource`. Si una prueba nueva necesitara DOORS para pasar, casi
siempre significa que la logica que quiere probar esta en la capa equivocada.

## Estructura

```
src/doors_kb/
  config.py          Configuracion por entorno, validada al arrancar
  models.py          Modelo de dominio y hash canonico
  errors.py          Errores con mensajes accionables
  sources/
    base.py          Protocolo RequirementsSource  <- la frontera del sistema
    fake.py          Fuente en memoria para pruebas y demos
    doors/
      dxl.py         Generacion de DXL (codigo puro, testeable en cualquier sitio)
      com_worker.py  Hilo COM unico, timeouts, reintentos
      client.py      RequirementsSource real sobre la sesion Automation
  db/
    schema.sql       Esquema de la copia local
    repository.py    Persistencia, hashes, borrado logico, historial
  sync/service.py    Algoritmo de sincronizacion segura
  servers/
    response.py      Limite y recorte de las respuestas MCP
    doors_server.py  Servidor MCP directo (H1)
  cli/sync_doors.py  Comando doors-sync
```

La regla estructural: **`db/` y `sync/` no conocen COM ni DXL**. Hablan del protocolo
`RequirementsSource`. Romper esa regla arrastraria Windows a toda la base de codigo.

## Convenciones

- **Idioma.** Documentacion, docstrings y comentarios en espanol; identificadores de codigo
  y nombres de tabla en ingles, como en la especificacion (`requirements`, `content_hash`).
- **Sin acentos en codigo, docs ni mensajes.** El CLI y los logs se leen en consolas de
  Windows con codificaciones heredadas donde los acentos salen mal; mantener la convencion
  en todo el proyecto evita mezclar dos estilos. La especificacion original (`especificacion.md`)
  se conserva tal cual, con sus acentos.
- **Docstring de modulo en todos los archivos**, indicando responsabilidad y requisitos que
  cubre. Un lector debe saber para que existe un archivo sin leer su cuerpo.
- **Los comentarios explican el porque**, no el que. Si algo no es obvio (por que se reactiva
  un registro con el mismo hash, por que el cursor no es un offset), el motivo va escrito.
- **Type hints completos** y `dataclasses` para el modelo. Sin metaprogramacion.
- **Errores accionables**: que fallo, sobre que modulo o atributo y que hacer. Nunca
  `except Exception: pass`.
- **Tests como documentacion**: nombre descriptivo y docstring que cita el requisito o
  criterio de aceptacion que verifican.

## Como anadir cosas

### Una herramienta MCP nueva

1. Anade el metodo al protocolo `RequirementsSource` (`sources/base.py`).
2. Implementalo en `FakeDoorsSource` **primero**: es lo que permite probarlo.
3. Genera el DXL en `sources/doors/dxl.py` y consumelo en `client.py`.
4. Registra la tool en `servers/doors_server.py` con `annotations=SOLO_LECTURA` y haz que
   devuelva a traves de `responder(...)`, para que respete el limite de tamano.
5. Anade la fila correspondiente a [`trazabilidad.md`](trazabilidad.md).

No anadas nunca una tool que ejecute DXL recibido del agente: es la unica linea roja del
diseno (RF-040, ADR-003).

### Un atributo nuevo en la sincronizacion

Basta con incluirlo en `DOORS_SYNC_ATTRIBUTES`; no hay que migrar el esquema (RF-053). Pero
tenlo en cuenta: **el perfil de atributos define el hash de contenido**. Anadir o quitar uno
cambia el hash de todos los requisitos del modulo, que pasaran a contar como `updated` en la
siguiente pasada (riesgo R-005). Conviene fijar el perfil antes de generar embeddings.

### El siguiente hito (H4, FTS5)

El sitio natural es una tabla `requirements_fts` en `db/schema.sql` alimentada desde
`repository.upsert_requirement`, y un `kb_server.py` en `servers/` que la consulte. La
clasificacion `inserted`/`updated`/`unchanged` que ya devuelve el sincronizador es la senal
para mantener el indice al dia sin reindexar todo.

## Que se prueba y que no

| Se prueba aqui | Requiere DOORS real |
|---|---|
| Clasificacion de cambios, borrado logico y reactivacion | Que el DXL generado se ejecute sin errores del interprete |
| Reglas de sincronizacion segura y cursor | Rendimiento por pagina en un modulo grande (CA-001, CA-006) |
| Generacion y escapado de DXL | Comportamiento de la sesion Automation y el login |
| Timeouts, envenenamiento y reintentos del worker | Trazabilidad entrante con permisos reales |
| Catalogo MCP, anotaciones y limites de respuesta | |

Lo de la derecha se verifica siguiendo [`operacion_windows.md`](operacion_windows.md).
