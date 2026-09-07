# Decisiones de arquitectura (ADR)

Cada decision registra **que** se decidio, **por que** y que consecuencias tiene. Las ocho primeras
provienen de la seccion 14 de la especificacion; la novena se acordo al arrancar esta implementacion.

---

## ADR-001 — Usar DOORS Automation + DXL

**Decision.** Acceder a DOORS Classic mediante la interfaz Automation (COM/OLE) `DOORS.Application`,
ejecutando scripts DXL generados desde Python.

**Motivacion.** Es la interfaz disponible para DOORS Classic de escritorio y permite ejecutar consultas
dentro de una sesion controlada. No existe una API REST equivalente en esta version del producto.

**Consecuencias.** El proceso Python debe correr en Windows, en la misma maquina que DOORS, con el mismo
usuario. Queda descartada la ejecucion desde WSL, contenedores de desarrollo o Remote SSH.

---

## ADR-002 — Crear una sesion Automation propia

**Decision.** Crear la sesion con `Dispatch("DOORS.Application")` en lugar de conectarse a una ventana ya
abierta con `GetActiveObject`.

**Motivacion.** Durante el desarrollo se comprobo que una ventana de DOORS abierta manualmente **no**
aparece de forma fiable como `DOORS.Application` en la Running Object Table de Windows. Ademas, la sesion
creada por COM no comparte el modulo abierto en la ventana manual, lo que producia errores `NO_MODULE`.

**Consecuencias.** El usuario debe autenticarse en la ventana que abre Python (no vale su sesion previa) y
el modulo se abre explicitamente por `fullName`. La espera del login necesita un timeout propio.

---

## ADR-003 — Solo lectura, sin tool de DXL arbitrario

**Decision.** Los modulos se abren con `read(...)` y el servidor MCP **no** expone ninguna herramienta que
permita al agente ejecutar DXL libre.

**Motivacion.** Reduce a cero el riesgo de que un agente modifique o borre la base de requisitos. DXL es un
lenguaje completo: una tool generica seria equivalente a dar acceso de escritura total.

**Consecuencias.** Cada capacidad nueva exige una tool especifica y revisada. Todo valor que Python inserta
en un script DXL se escapa antes de concatenarse.

---

## ADR-004 — SQLite como replica local

**Decision.** Mantener una copia de consulta de los requisitos en SQLite.

**Motivacion.** Es sencillo, portable, transaccional y suficiente para construir FTS y RAG sin
infraestructura adicional. Desacopla las busquedas frecuentes del agente de la latencia y del coste DXL.

**Consecuencias.** La base local es un **derivado reconstruible**; DOORS sigue siendo la fuente de verdad.
Aparece el problema de la frescura: el sistema debe poder informar de cuando se sincronizo por ultima vez.

---

## ADR-005 — Hash de contenido por requisito

**Decision.** Calcular un SHA-256 sobre una representacion canonica de los campos sincronizados.

**Motivacion.** Permite clasificar cada requisito como `inserted`, `updated` o `unchanged` sin comparar
campo a campo, y sera la senal que evite recalcular embeddings de objetos que no han cambiado.

**Consecuencias.** El hash depende del conjunto de atributos elegido: cambiar ese conjunto invalida todos
los hashes del modulo (riesgo R-005). Conviene fijar un perfil de atributos estable por modulo antes de
generar embeddings.

---

## ADR-006 — Paginacion por cursor, nunca por offset

**Decision.** Recorrer los modulos con un cursor basado en el ultimo Absolute Number visitado y
`next(Object)`, en lugar de saltar N objetos desde el principio.

**Motivacion.** La primera implementacion usaba offset: cada pagina volvia a recorrer todos los objetos
anteriores, con coste cuadratico creciente que acababa agotando el watchdog interno de DXL
(*DXL Execution Timeout*) en modulos grandes.

**Consecuencias.** El coste por pagina es aproximadamente constante. A cambio, el recorrido es
estrictamente secuencial: no se puede saltar a una pagina arbitraria sin recorrer las anteriores.

---

## ADR-007 — FTS antes que embeddings

**Decision.** Implementar SQLite FTS5 antes de la busqueda semantica.

**Motivacion.** Da una linea base lexical rapida para identificadores, terminos exactos y vocabulario
tecnico, que es donde la busqueda vectorial suele rendir peor. Con esa linea base se puede medir que
aporta realmente la semantica y separar problemas de datos de problemas vectoriales.

**Consecuencias.** El hito de embeddings se retrasa deliberadamente. Ambos son hitos posteriores a esta
entrega (H4 y H5).

---

## ADR-008 — Separar cliente, sincronizacion, repositorio y MCP

**Decision.** Cuatro capas con fronteras explicitas: acceso a DOORS, servicio de sincronizacion,
persistencia y servidores MCP.

**Motivacion.** Mejora la testabilidad y permite sustituir la fuente o el indice en el futuro. En concreto,
que la sincronizacion dependa de un **protocolo** `RequirementsSource` y no de COM es lo que hace posible
probar toda su logica sin abrir DOORS (RNF-013).

**Consecuencias.** Hay que mantener la fuente falsa fiel a la semantica real de DOORS: si divergen, los
tests dejan de demostrar lo que dicen demostrar.

---

## ADR-009 — Embeddings mediante API compatible con OpenAI

**Decision.** Cuando se aborde el hito H5, los embeddings se generaran llamando a una API de un tercero que
implementa el **protocolo de OpenAI**, configurada mediante `base_url`, `api_key` y nombre de modelo.

**Motivacion.** Evita cargar un modelo pesado en la maquina de DOORS y permite cambiar de proveedor sin
tocar el codigo, porque el contrato HTTP es el mismo.

**Consecuencias.** La maquina que genere embeddings necesitara salida de red hacia ese endpoint, lo que
puede estar restringido en entornos de gestion de requisitos; la generacion podria tener que ejecutarse en
una maquina distinta de la que habla con DOORS. Cada embedding almacenara el modelo con el que se genero,
junto al hash de contenido, para poder detectar y rehacer indices obsoletos (RF-073).

**Estado.** Decidida, **no implementada** en esta entrega (alcance H0-H3).
