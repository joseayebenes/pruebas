# Tutorial: DOORS → SQLite → Embeddings
## Paso 1 — Construir la copia local

En este primer paso todavía **no usamos embeddings**. Primero necesitamos una copia local fiable, capaz de representar requisitos de varios módulos y detectar qué elementos han cambiado.

## Arquitectura objetivo

```text
DOORS Classic
     │
     │ COM + DXL
     ▼
Sincronizador Python
     │
     ▼
SQLite
 ├── modules
 ├── requirements
 ├── requirement_attributes
 ├── links
 └── sync_runs
     │
     ├────────────► búsqueda textual
     │
     └────────────► embeddings
                        │
                        ▼
                    MCP / IA
```

DOORS seguirá siendo la **fuente de verdad**. SQLite será una copia optimizada para consultas del agente.

## Por qué empezar por SQLite

Si preguntamos a DOORS en cada consulta, dependemos continuamente de COM, DXL, la sesión abierta y posibles diálogos modales. Con una copia local podemos separar dos operaciones:

```text
sincronizar datos        consultar datos
      │                       │
     DOORS                  SQLite
```

La IA podrá hacer muchas consultas contra SQLite y solo necesitaremos acceder a DOORS para refrescar la información.

## Archivos de este paso

```text
models.py
repository.py
demo_step1.py
requirements_step1.txt
```

### `models.py`

Contiene `RequirementRecord`, nuestro modelo neutral. No depende de DOORS ni de SQLite. Más adelante el extractor DXL convertirá cada objeto de DOORS en uno de estos registros.

### `repository.py`

Contiene la capa de persistencia SQLite.

Las tablas principales son:

- `modules`: módulos formales sincronizados.
- `requirements`: datos principales de cada objeto.
- `requirement_attributes`: atributos personalizados.
- `links`: preparada para trazabilidad.
- `sync_runs`: preparada para registrar estadísticas de sincronización.

Cada requisito se identifica de forma única mediante:

```text
(module_path, absolute_number)
```

Esto es importante porque un `Absolute Number` solo es único dentro de su módulo.

## Atributos personalizados

DOORS permite que cada proyecto tenga atributos diferentes. En vez de añadir una columna SQL por cada atributo, los almacenamos así:

```text
requirement_id | name                | value_text
---------------+---------------------+-----------
42             | Status              | Approved
42             | Priority            | High
42             | Verification Method | Test
```

`Object Heading` y `Object Text` sí tienen columnas propias porque serán fundamentales para búsqueda y embeddings.

## Detección de cambios mediante hash

Para cada requisito calculamos un SHA-256 a partir de sus datos relevantes:

```text
heading
text
outline_number
identifier
attributes
estado eliminado
fecha de modificación de origen
```

Si en una sincronización posterior el hash es igual:

```text
DOORS hash AAA == SQLite hash AAA
                 ↓
              unchanged
```

no hay que regenerar el embedding.

Si cambia:

```text
DOORS hash BBB != SQLite hash AAA
                 ↓
               updated
                 ↓
          regenerar embedding
```

Este mecanismo será muy importante cuando haya miles de requisitos.

## Ejecutar la demo

Desde PowerShell:

```powershell
cd C:\ruta\doors_rag_step1
python .\demo_step1.py
```

La primera ejecución muestra aproximadamente:

```text
SYS-REQ-1: inserted
SYS-REQ-2: inserted
SYS-REQ-3: inserted
```

Y crea:

```text
doors_requirements.db
```

Ejecuta una segunda vez:

```powershell
python .\demo_step1.py
```

Ahora debería aparecer:

```text
SYS-REQ-1: unchanged
SYS-REQ-2: unchanged
SYS-REQ-3: unchanged
```

Eso demuestra que la detección de cambios funciona.

## Probar una actualización

En `demo_step1.py`, cambia por ejemplo:

```python
"Status": "Draft"
```

por:

```python
"Status": "Approved"
```

para `SYS-REQ-2`.

Al ejecutar otra vez, deberías obtener:

```text
SYS-REQ-1: unchanged
SYS-REQ-2: updated
SYS-REQ-3: unchanged
```

## Por qué aún no añadimos embeddings

Mantendremos responsabilidades separadas:

```text
Paso 1  Persistencia SQLite
Paso 2  Sincronización real desde DOORS
Paso 3  Búsqueda textual local con FTS
Paso 4  Embeddings
Paso 5  Búsqueda híbrida
Paso 6  Tools MCP sobre la base local
Paso 7  Links y análisis de trazabilidad
```

Así, si algo falla, sabremos en qué capa está el problema.

## Siguiente paso

El siguiente componente será un sincronizador real que utilice el acceso a DOORS que ya tenemos.

La idea será:

```text
DOORS: página 0..99
       ↓
RequirementRecord
       ↓
SQLite

DOORS: página 100..199
       ↓
RequirementRecord
       ↓
SQLite
```

Al finalizar tendremos estadísticas como:

```text
seen       = 1248
inserted   = 12
updated    = 7
unchanged  = 1229
removed    = 3
```

Solo `inserted` y `updated` necesitarán generar o regenerar embeddings más adelante.
