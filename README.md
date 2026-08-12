# A380 · Guía interactiva

Web interactiva de una sola página sobre el Airbus A380, el mayor avión de
pasajeros jamás construido.

## Contenido

- **Anatomía** — plano lateral esquemático con 7 puntos interactivos y panel
  de datos estilo pantalla de a bordo (ECAM).
- **Cifras** — tablero de datos clave con contadores animados.
- **Comparativa** — gráfico de barras con métrica seleccionable (pasajeros,
  MTOW, envergadura, longitud) frente al 747-8, 777-9 y A350-1000.
- **Operadores** — las 251 entregas repartidas entre 14 aerolíneas.
- **Cronología** — del lanzamiento del programa (2000) a la última entrega (2021).

## Uso

Es un único `index.html` autocontenido, sin dependencias externas.
Basta con abrirlo en un navegador:

```
open index.html
```

Incluye tema claro y oscuro (automático según el sistema, con conmutador
manual en la cabecera) y respeta `prefers-reduced-motion`.
