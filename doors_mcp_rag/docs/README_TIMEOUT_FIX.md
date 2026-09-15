# Paso 2.1 — Corregir `DXL Execution Timeout`

La ventana no la genera Python. Es el watchdog interno del intérprete DXL.

## 1. Timeout interno de DXL

Cada script generado empieza ahora con:

```dxl
pragma runLim, 0
```

`0` desactiva el límite interno de ciclos DXL. El timeout externo de Python
sigue activo mediante `future.result(timeout=N)`.

Puedes volver a usar un límite interno finito con:

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "10000000"
```

## 2. Paginación por cursor

Antes se usaba un `offset`. Para obtener la página N, DXL volvía a recorrer
todos los objetos anteriores.

Ahora cada respuesta devuelve:

```json
{
  "next_after_absolute_number": 417
}
```

La siguiente página comienza directamente después de ese objeto:

```dxl
Object cursorObject = object(417, currentModule)
Object obj = next(cursorObject)
```

Antes de navegar se fija un display set estable:

```dxl
filtering off
level 0
sorting off
```

## 3. Valores predeterminados más pequeños

- page size: 50
- caracteres máximos por atributo: 20 000

Para la primera prueba usa incluso 25:

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"
$env:DOORS_DXL_TIMEOUT_SECONDS = "90"

python .\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --page-size 25 `
  --max-attribute-chars 20000
```

Primero puedes verificar la lógica sin DOORS:

```powershell
python .\test_sync_fake.py
```
