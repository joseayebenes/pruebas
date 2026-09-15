# Paso 2.2 — Corrección del error de parseo DXL

## Síntoma

DOORS mostraba:

```text
E-DXL: <Line:1> incorrect arguments for (\)
E-DXL: <Line:2> pragma (perm) expects an int argument
```

## Causa

La función Python generaba accidentalmente:

```python
return f"pragma runLim, {DXL_RUN_LIMIT_CYCLES}\\n"
```

Ese `\\n` produce dos caracteres literales:

```text
\
n
```

Por tanto, el DXL enviado a DOORS quedaba conceptualmente así:

```text
pragma runLim, 0\nstring jsonEscape(...)
```

y DOORS no podía analizarlo.

## Corrección

Ahora es:

```python
return f"pragma runLim, {DXL_RUN_LIMIT_CYCLES}\n"
```

El string generado contiene un salto de línea real:

```text
pragma runLim, 0
string jsonEscape(...)
```

## Prueba

Ejecuta:

```powershell
python .\test_dxl_generation.py
```

Debe mostrar:

```text
PRUEBA OK
repr(preamble): 'pragma runLim, 0\n'
```

Después prueba la sincronización:

```powershell
$env:DOORS_DXL_RUN_LIMIT_CYCLES = "0"

python .\sync_doors.py `
  --module "/Proyecto/Requisitos/Requisitos del sistema" `
  --page-size 25 `
  --max-attribute-chars 20000
```
