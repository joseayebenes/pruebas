# Arquitectura

## 1. Vision general

El sistema tiene dos caminos de lectura hacia los requisitos, y estan pensados para usarse juntos:

- **Camino directo** (MCP -> DOORS): informacion siempre actual, latencia alta, requiere Windows + DOORS.
- **Camino local** (MCP -> SQLite): latencia baja y busquedas masivas, pero refleja la ultima
  sincronizacion, no el estado vivo de DOORS.

```text
                       +-------------------------+
                       |     IBM DOORS Classic   |
                       |     fuente de verdad    |
                       +------------+------------+
                                    |
                              COM + DXL (lectura)
                                    |
                       +------------v------------+
                       |  sources/doors/client   |  DoorsComClient
                       |  worker COM / timeouts  |
                       +------------+------------+
                                    |
                     RequirementRecord (protocolo RequirementsSource)
                                    |
              +---------------------+---------------------+
              |                                           |
   +----------v-----------+                    +----------v-----------+
   |   sync/service.py    |                    | servers/doors_server |
   | altas/cambios/bajas  |                    |  MCP directo (H1)    |
   +----------+-----------+                    +----------------------+
              |
   +----------v-----------+
   |   db/repository.py   |
   |  SQLite (WAL)        |
   | requirements/attrs   |
   | links / sync_runs    |
   +----------+-----------+
              |
       (H4) FTS5   (H5) embeddings   -> (H6) hibrida -> (H7) MCP sobre la KB
```

Las capas por debajo de la linea de puntos (FTS5 en adelante) son hitos futuros; el esquema SQLite ya
esta preparado para recibirlas sin migraciones destructivas.

## 2. Modulos y responsabilidades

| Modulo | Responsabilidad | Depende de |
|---|---|---|
| `config.py` | Leer y validar la configuracion del entorno; fallar al arrancar si algo es invalido | — |
| `models.py` | Modelo de dominio y hash canonico de contenido | — |
| `errors.py` | Jerarquia de errores con mensajes accionables | — |
| `sources/base.py` | Protocolo `RequirementsSource`: la frontera con DOORS | `models` |
| `sources/fake.py` | Implementacion en memoria del protocolo, para pruebas y demos | `sources/base` |
| `sources/doors/dxl.py` | Generar y escapar scripts DXL (codigo puro, sin COM) | — |
| `sources/doors/com_worker.py` | Serializar COM en un hilo, timeouts, reintentos, bloqueo | `pywin32` (perezoso) |
| `sources/doors/client.py` | `RequirementsSource` real sobre la sesion Automation | `dxl`, `com_worker` |
| `db/repository.py` | Persistencia SQLite, hashes, borrado logico, historial | `sqlite3`, `models` |
| `sync/service.py` | Orquestar paginas y aplicar las reglas de sincronizacion segura | `sources/base`, `db` |
| `servers/doors_server.py` | Exponer tools MCP de solo lectura sobre DOORS | `mcp`, `sources` |
| `cli/sync_doors.py` | Entrada por linea de comandos de la sincronizacion | `sync`, `db`, `sources` |

La regla que sostiene el diseno: **`sync/` y `db/` no conocen COM ni DXL**. Solo hablan del protocolo
`RequirementsSource`. Por eso toda la logica critica de sincronizacion se prueba sin DOORS (RNF-013).

## 3. Flujo de una sincronizacion completa

```text
  SyncService.sync_module(modulo)
    |
    |-- 1. validate_attributes(...)        <- si falla, aborta ANTES de escribir nada (RF-060)
    |-- 2. repository.start_sync_run()     <- estado "running"
    |
    |-- 3. bucle de paginas (cursor):
    |        fetch_page(cursor) --> [RequirementRecord]
    |          |
    |          +-- por pagina, en UNA transaccion:
    |                upsert_requirement(r) -> inserted | updated | unchanged
    |                seen.add(absolute_number)
    |          |
    |          +-- cursor = next_cursor;  si next_cursor is None -> fin del modulo
    |
    |-- 4. SOLO si se llego al final:
    |        mark_missing_as_deleted(modulo, seen)          (RF-061)
    |
    +-- 5. finish_sync_run("success", stats)
             excepcion en cualquier punto -> finish_sync_run("failed", error)
                                             y NO se marca nada como eliminado
```

El punto 4 es la regla de seguridad mas importante del sistema: una sincronizacion interrumpida a mitad
ha visto solo una parte del modulo, y marcar como eliminado "lo que no aparecio" borraria logicamente
requisitos que si existen. Por eso el marcado depende de haber alcanzado el final del recorrido, no de
que no haya habido errores.

## 4. Deteccion incremental de cambios

```text
DOORS                        SQLite                     Clasificacion
hash A                  ==   hash A                ->   unchanged   (no se re-embebe)
hash B                  !=   hash A                ->   updated     (regenerar embedding)
no existe localmente                               ->   inserted    (crear embedding)
existe local borrado    ==   hash A  (reaparece)   ->   updated     (reactivar)
desaparece tras sync completa                      ->   deleted     (retirar del indice)
```

El cuarto caso es el que un `WHERE content_hash != ?` ingenuo pierde: un requisito borrado en DOORS y
restaurado tal cual vuelve con el **mismo** hash. Si se clasificara como `unchanged`, el registro local
se quedaria con `is_deleted = 1` para siempre y el requisito desapareceria de las busquedas (RF-056).

## 5. Estados del worker COM

Todas las llamadas COM se serializan en un unico hilo con su propio apartamento (`CoInitialize`), porque
las tools MCP pueden invocarse desde hilos distintos y el objeto de DOORS no es seguro entre hilos
(RNF-002).

```text
                   +----------+
   crear worker -> |  ready   |
                   +----+-----+
                        |
             call(fn, timeout)
                        |
            +-----------+------------+
            |                        |
       resultado                timeout expira
            |                        |
       +----v----+            +------v-------+
       |  ready  |            |   poisoned   |
       +---------+            +------+-------+
                                     |
                       toda llamada posterior falla con
                       WorkerPoisonedError: DXL puede seguir
                       corriendo dentro de DOORS y reutilizar
                       la sesion daria resultados incoherentes.
                       Requiere reiniciar el proceso (RNF-005).
```

Aparte del timeout, los errores COM de "DOORS ocupado / reintente mas tarde"
(`RPC_E_CALL_REJECTED`, `RPC_E_SERVERCALL_RETRYLATER`) se reintentan con espera creciente: son
transitorios y ocurren cuando el usuario esta interactuando con la ventana de DOORS (RNF-006).

## 6. Control del tamano de las respuestas

El servidor MCP habla por **stdio**: stdout esta reservado al protocolo, de modo que todo el logging va a
stderr (RNF-009). Escribir en stdout corromperia la sesion MCP.

Toda respuesta pasa por un unico envoltorio que serializa a JSON y aplica el limite duro configurable:

1. Si cabe, se devuelve tal cual.
2. Si no cabe, se truncan los textos largos y se recorta la lista de resultados, marcando el recorte de
   forma explicita en la respuesta.
3. Si aun asi no cabe, se devuelve un error de tamano que indica que parametro reducir.

Nunca se devuelve una respuesta truncada en silencio: el agente debe poder distinguir "no hay mas
resultados" de "hay mas, pero no caben" (RF-043, RF-044).
