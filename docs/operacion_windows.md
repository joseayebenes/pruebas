# Puesta en marcha con DOORS real

Esta guia cubre lo que no se puede probar fuera de Windows: la sesion Automation, la
ejecucion del DXL generado y el rendimiento en un modulo grande. Corresponde al cierre del
hito H3.

## Requisitos previos

- Windows con **IBM DOORS Classic instalado** en la misma maquina.
- Python 3.11 o superior, ejecutandose con **el mismo usuario de Windows** que DOORS y con
  un nivel de privilegios compatible (si DOORS va como administrador, Python tambien).
- **No sirve** WSL, un contenedor de desarrollo ni Remote SSH: el cliente COM necesita la
  sesion de escritorio real.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,win]"
```

## 1. Comprobar la instalacion sin tocar DOORS

Antes de involucrar a DOORS, conviene descartar problemas del propio proyecto:

```powershell
.\.venv\Scripts\pytest -q
.\.venv\Scripts\python examples\demo_sync_fake.py
```

Si esto falla, el problema no esta en DOORS.

## 2. Abrir la sesion Automation

```powershell
$env:DOORS_MODULE_PATH = "/Proyecto/Requisitos/Requisitos del sistema"
.\.venv\Scripts\doors-sync --source doors --module $env:DOORS_MODULE_PATH
```

Python abre **una ventana nueva** de DOORS. Hay que autenticarse **en esa ventana**, no en
una que ya tuvieras abierta: la sesion de Automation es independiente de la sesion manual
(ADR-002). Si tarda mas de 30 segundos, sube `DOORS_START_TIMEOUT_SECONDS`.

## 3. Primera sincronizacion

Configuracion recomendada para la primera pasada de un modulo grande:

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"    # desactiva el watchdog interno de DXL
$env:DOORS_DXL_TIMEOUT_SECONDS  = "90"   # el control lo lleva el timeout de Python

.\.venv\Scripts\doors-sync `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --page-size 25 `
  --max-attribute-chars 20000 `
  --db .\doors_kb.sqlite3 `
  --verbose
```

`--verbose` imprime una linea por pagina con su cursor: es como se comprueba CA-006 (la
duracion por pagina debe mantenerse estable segun avanza el modulo, no crecer).

## 4. Verificar los criterios de aceptacion

| Criterio | Como comprobarlo |
|---|---|
| CA-001 | La sincronizacion completa termina sin ventanas *DXL Execution Timeout* |
| CA-002 | Lanzarla dos veces seguidas: la segunda da `inserted=0, updated=0, unchanged=N` |
| CA-003 | Editar el texto de un objeto en DOORS y volver a sincronizar: `updated=1` |
| CA-004 | Crear un objeto (`inserted=1`); borrar otro y sincronizar entero (`deleted=1`) |
| CA-005 | Cortar la sincronizacion con Ctrl+C a mitad: `sync_runs.status='failed'` y `deleted=0` |
| CA-006 | Con `--verbose`, la duracion por pagina se mantiene estable |
| CA-007 | Lanzarla con `--attributes "Object Text,No Existe"`: falla antes de escribir nada |

Consulta del historial:

```powershell
sqlite3 .\doors_kb.sqlite3 "SELECT status, requirements_seen, inserted, updated, unchanged, deleted, completed_module, error FROM sync_runs ORDER BY id DESC LIMIT 5;"
```

## 5. Usar el servidor MCP desde VS Code

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
    }
  }
}
```

Orden de uso recomendado para el agente: `doors_configuration` -> `start_doors_session`
(el usuario se autentica) -> `doors_status` -> `list_object_attributes` -> consultas.

---

# Fallos conocidos y que hacer

Todos ocurrieron durante el desarrollo del proyecto (seccion 10 de la especificacion).

### No encuentra la sesion de DOORS que tengo abierta

**No es un fallo.** El proyecto nunca se conecta a una ventana existente: crea la suya con
`Dispatch`, porque una ventana abierta a mano no aparece de forma fiable en la Running
Object Table de Windows (ADR-002). Autenticate en la ventana que abre Python.

### `NO_MODULE` al consultar

La sesion de Automation no comparte el modulo que tengas abierto en tu ventana. Comprueba
que la ruta es el `fullName` completo del modulo, con las barras y mayusculas exactas. En
DOORS: clic derecho sobre el modulo -> Propiedades -> ruta completa.

### `DXL Execution Timeout`

El watchdog interno de DXL corto el script. Opciones, por orden:

1. Asegurate de que `DOORS_DXL_RUN_LIMIT_CYCLES=0` (desactiva el watchdog y deja el control
   al timeout de Python, que si distingue un script colgado de uno que va lento).
2. Baja `--page-size` (por ejemplo a 10).
3. Baja `--max-attribute-chars` si el modulo tiene objetos con textos enormes.

Si el tiempo por pagina **crece** segun avanza el modulo, algo ha vuelto a paginar por
offset: revisa `dxl.py::_posicionar_cursor` (ADR-006).

### "El worker COM esta bloqueado desde una llamada anterior"

Una llamada supero su timeout y el script DXL puede seguir corriendo dentro de DOORS.
Reutilizar esa sesion podria mezclar respuestas, asi que el proceso queda inutilizable a
proposito (RNF-005): **reinicia el proceso** (o el servidor MCP). Si se repite, sube
`DOORS_DXL_TIMEOUT_SECONDS` o baja el tamano de pagina.

### Un atributo devuelve siempre vacio

Casi siempre es un nombre mal escrito. Los nombres de DOORS distinguen mayusculas y llevan
espacios: `"Object Text"`, no `"Object text"`. Usa `list_object_attributes` o
`validate_attributes`, que sugiere el nombre correcto. El proyecto valida contra el esquema
del modulo justo para que esto sea un error y no una cadena vacia silenciosa.

### DOORS responde "ocupado" o rechaza la llamada

Ocurre cuando estas interactuando con la ventana de DOORS mientras corre la sincronizacion.
El worker lo reintenta con espera creciente (RNF-006). Si persiste, cierra los dialogos
abiertos en DOORS y deja la ventana quieta.

### El servidor MCP no arranca o el cliente lo da por caido

Comprueba que nada escribe en **stdout**: con transporte stdio, stdout es del protocolo y
cualquier `print` corrompe la sesion. Los logs del proyecto van a stderr (RNF-009).

### Respuestas truncadas en el agente

Es intencionado (RF-044). El campo `truncated` de la respuesta dice cuantos resultados se
omitieron. Reduce `limit`, reduce `max_attribute_chars`, o continua el recorrido con el
`next_cursor` que devuelve la propia respuesta.
