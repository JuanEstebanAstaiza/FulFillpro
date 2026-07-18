# FulfillPro — Especificación Técnica Completa del Motor de Procesamiento

**Versión:** 1.0 · **Propósito:** Este documento describe el proceso completo con el nivel de detalle necesario para reconstruir el sistema desde cero, en cualquier lenguaje de programación. Cada fase indica su entrada, su salida, el algoritmo exacto y los casos borde.

---

## Visión general del flujo

El sistema recibe **un archivo Excel** (reporte de órdenes de la plataforma dropshipping) y produce **un archivo Excel** con tres hojas: `Resumen`, `Reporte Ordenado` y `PRIORITARIAS`. El procesamiento ocurre en 14 fases estrictamente secuenciales:

```
ENTRADA (.xlsx)
   │
   ├─ FASE 1  Lectura del archivo
   ├─ FASE 2  Detección de columnas por nombre
   ├─ FASE 3  Limpieza y conversión de tipos
   ├─ FASE 4  Ordenamiento inicial por producto
   │
   ├─ RUTA A: RESUMEN ──────────────────────────
   │   ├─ FASE 5  Agrupación por número de guía
   │   ├─ FASE 6  Detección de combos
   │   ├─ FASE 7  Construcción de claves y conteo de cantidades
   │   ├─ FASE 8  Generación de la columna VARIABLES
   │   ├─ FASE 9  Unificación de duplicados
   │   ├─ FASE 10 Limpieza de nombres (paréntesis)
   │   └─ FASE 11 Orden final y limpieza de ceros
   │
   ├─ RUTA B: REPORTE ORDENADO ─────────────────
   │   └─ FASE 12 Extracción de 3 columnas ordenadas
   │
   ├─ RUTA C: PRIORITARIAS ─────────────────────
   │   └─ FASE 13 Cálculo de retrasos y riesgo
   │
   └─ FASE 14 Construcción del Excel de salida con formato
   │
SALIDA (.xlsx con 3 hojas)
```

---

## FASE 1 — Lectura del archivo

**Entrada:** archivo `.xlsx` o `.xls` subido por el usuario.
**Salida:** matriz de datos en memoria (filas × columnas).

Reglas exactas:

1. Se toma únicamente la **primera hoja** del libro. Las demás hojas se ignoran.
2. La **fila 1** se interpreta como encabezados. Las filas 2 en adelante son datos.
3. **Todos los valores se leen como texto** (`dtype=str` en pandas, `raw:false` en SheetJS). Esto es crítico: los IDs de orden y números de guía son enteros largos (ej. `240049656479`) y si se leen como número, el motor puede convertirlos a notación científica (`2.40049E+11`) y corromperlos.
4. Se recortan espacios en blanco de los nombres de encabezado: `"  PRODUCTO "` → `"PRODUCTO"`.
5. Se descartan las filas completamente vacías (todas sus celdas vacías o nulas).

**Validación:** si tras esto quedan menos de 1 fila de datos, se aborta con el error: *"El archivo está vacío o no tiene datos"*.

---

## FASE 2 — Detección de columnas por nombre

**Entrada:** lista de encabezados de la fila 1.
**Salida:** diccionario `C` que mapea cada campo lógico → índice de columna real.

Este es el mecanismo que hace al sistema **robusto ante cambios de layout**. El VBA original usaba letras fijas (K = guía, AD = producto, AF = cantidad); si la plataforma insertaba una columna nueva, todo se rompía. Aquí las columnas se buscan por nombre.

### 2.1 Diccionario de sinónimos

Cada campo lógico tiene una lista de nombres candidatos, en orden de prioridad:

```
id        → [ID, ORDEN, ORDER ID, ID ORDEN]
guia      → [NÚMERO GUIA, NUMERO GUIA, GUIA, N° GUIA, TRACKING]
producto  → [PRODUCTO, PRODUCT, NOMBRE PRODUCTO, ITEM]
variacion → [VARIACION, VARIACIÓN, VARIATION, TALLA, COLOR]
cantidad  → [CANTIDAD, QTY, QUANTITY, CANT]
valor     → [TOTAL DE LA ORDEN, TOTAL ORDEN, VALOR, PRECIO, TOTAL]
fechaGuia → [FECHA GUIA GENERADA, FECHA GUIA, FECHA DE ENVIO, SHIP DATE]
```

Para agregar compatibilidad con un reporte nuevo en el futuro, basta con añadir el nombre de su columna a la lista correspondiente. No se toca ninguna otra parte del código.

### 2.2 Función de normalización

Antes de comparar, tanto el encabezado real como el candidato pasan por esta normalización:

1. Convertir a MAYÚSCULAS.
2. Recortar espacios laterales.
3. Reemplazar tildes y eñes: `Á→A, É→E, Í→I, Ó→O, Ú→U, Ñ→N`.

Así, `"Número Guía"`, `"NUMERO GUIA"` y `"número guia "` se consideran idénticos. Esto resuelve el problema real de que la plataforma a veces exporta con tildes y a veces sin ellas.

### 2.3 Algoritmo de búsqueda

```
PARA cada campo lógico (id, guia, producto, ...):
    PARA cada candidato en su lista (en orden):
        PARA cada encabezado del archivo:
            SI normalizar(encabezado) == normalizar(candidato):
                C[campo] = índice de ese encabezado
                pasar al siguiente campo
    SI ningún candidato coincidió:
        C[campo] = NO_ENCONTRADO (-1 o null)
```

La comparación es de **igualdad exacta** tras normalizar, no de "contiene". Esto evita falsos positivos (ej. que "PRECIO PROVEEDOR" coincida con "PRECIO").

### 2.4 Validación de columnas obligatorias

Dos columnas son indispensables. Si falta alguna, se aborta con mensaje específico:

- `producto` no encontrado → *"No se encontró la columna PRODUCTO. Verifica el archivo."*
- `guia` no encontrado → *"No se encontró la columna NÚMERO GUIA. Verifica el archivo."*

Las demás columnas son opcionales: si falta `variacion`, todo funciona sin variaciones; si falta `fechaGuia`, la hoja PRIORITARIAS sale vacía; si falta `valor`, los riesgos se calculan en $0.

---

## FASE 3 — Limpieza y conversión de tipos

**Entrada:** matriz cruda + mapa de columnas `C`.
**Salida:** lista de registros limpios, un objeto por fila.

Por cada fila de datos se construye un registro con esta transformación exacta:

| Campo | Transformación | Si el valor es inválido |
|---|---|---|
| `id` | texto, trim | `""` |
| `guia` | texto, trim | `""` |
| `producto` | texto, trim | `""` |
| `variacion` | texto, trim | `""` |
| `cantidad` | convertir a entero | `0` (y más adelante se fuerza mínimo `1`) |
| `valor` | convertir a decimal | `0` |
| `fechaGuia` | texto, trim (se parsea después, en Fase 13) | `""` |

Además se descartan los registros donde **tanto** `producto` **como** `guia` estén vacíos (filas basura del reporte).

Detalle importante sobre `variacion`: los valores literales `"nan"` (que pandas produce al convertir nulos a texto) deben tratarse como vacío. La condición correcta es: `variacion ≠ "" Y variacion ≠ "nan"`.

---

## FASE 4 — Ordenamiento inicial por producto

**Equivalente VBA:** Módulo 1 (`FiltrarYOrdenarColumnaAD`).

Se ordenan todos los registros **alfabéticamente por el nombre del producto**, ascendente (A→Z). Este orden se hereda en el Reporte Ordenado y hace que productos iguales queden contiguos.

---

## FASE 5 — Agrupación por número de guía

**Equivalente VBA:** primera mitad del Módulo 2.
**Entrada:** registros limpios y ordenados.
**Salida:** diccionario `porGuia`: número de guía → lista de registros con esa guía.

```
porGuia = {}
PARA cada registro:
    SI registro.guia == "": saltar (no entra al Resumen)
    porGuia[registro.guia].agregar(registro)
```

**Regla clave:** una fila sin número de guía **no participa del Resumen** (porque el resumen se construye por guía), pero **sí aparece** en el Reporte Ordenado, que se arma desde los datos originales.

---

## FASE 6 — Detección de combos (órdenes compuestas)

**Definición exacta de combo:** una guía es combo si su lista en `porGuia` contiene **más de una fila**, sin importar si los productos son iguales o distintos.

```
combosDetectados = { guia : porGuia[guia].length > 1 }
```

Ejemplo real del archivo de prueba: la orden `71265863` tiene dos filas con el mismo producto "Almohada Ortopédica Premium Quality" (cantidad 1 cada una) bajo la guía `240049656479`. Como son 2 filas bajo la misma guía → **es combo**, aunque el producto se repita. Esto replica fielmente el comportamiento del VBA (`subDict.Count > 1`).

---

## FASE 7 — Construcción de claves y conteo de cantidades

**Equivalente VBA:** segunda mitad del Módulo 2. Esta es la fase más importante del sistema; define la estructura del Resumen.

### 7.1 Estructuras de datos

```
resumenDict : claveFinal → { cantidad → númeroDeÓrdenes }
nombreDict  : claveFinal → texto a mostrar en la columna PRODUCTO
cantMax     : entero, la cantidad más grande vista (mínimo 1)
```

### 7.2 Procesamiento por guía

Se recorre cada guía de `porGuia` y se decide su representación:

**CASO A — La guía ES combo:**

```
claveFinal        = "COMP-" + númeroDeGuía
cantidadResumen   = 1                          ← SIEMPRE 1, sin excepción
nombreParaMostrar = "ProductoA (cantA) + ProductoB (cantB) + ..."
```

El nombre concatena cada línea del combo con su cantidad entre paréntesis, separadas por `" + "`. Ejemplo: `"Almohada Ortopédica Premium Quality (1) + Almohada Ortopédica Premium Quality (1)"`.

La cantidad se fija en 1 porque para bodega **un combo es una unidad de alistamiento**: se empaca todo junto bajo una guía.

**CASO B — La guía es orden normal (una sola fila):**

```
SI variacion ≠ "" y variacion ≠ "nan":
    claveFinal = id + "|" + variacion        ← el separador es la barra vertical |
SI NO:
    claveFinal = id

cantidadResumen   = cantidad de la fila (si es 0 o inválida → usar 1)
nombreParaMostrar = producto de la fila
```

**Nota crítica sobre el `id`:** aquí `id` es el **identificador del producto** (en el reporte original, la columna que el VBA leía en AA = `PRODUCTO ID`), no el ID de la orden. La clave agrupa "mismo producto + misma variación", lo cual permite que órdenes distintas del mismo producto se acumulen en la misma fila del Resumen.

### 7.3 Acumulación del conteo

```
resumenDict[claveFinal][cantidadResumen] += 1
SI cantidadResumen > cantMax: cantMax = cantidadResumen
```

### 7.4 Semántica del resultado (leer con atención)

La estructura final significa:

> *"Del producto X (fila), hay **N órdenes** (valor de la celda) que piden **c unidades** cada una (columna Cantidad c)."*

Ejemplo: si la fila "Almohada Ortopédica" tiene un `5` en la columna `Cantidad 1` y un `2` en `Cantidad 3`, se lee: **5 órdenes de 1 almohada + 2 órdenes de 3 almohadas** = bodega debe alistar 5 paquetes individuales y 2 paquetes de a tres. Total de unidades físicas: 5×1 + 2×3 = 11.

`cantMax` define cuántas columnas `Cantidad N` tendrá la hoja: si la orden más grande pide 4 unidades, habrá columnas `Cantidad 1` a `Cantidad 4`.

**Guarda de seguridad recomendada:** limitar `cantMax` a un máximo razonable (ej. 60 columnas). Si un dato corrupto trae cantidad 5000, sin la guarda el Excel explota en columnas.

---

## FASE 8 — Generación de la columna VARIABLES

**Equivalente VBA:** Módulos 3 y 4 (`OrdenarResumenAutomatico` + `OrganizarResumenFinal`).

Se transforma cada `claveFinal` en el texto visible de la primera columna:

```
SI la clave contiene "|":
    VARIABLES = lo que está DESPUÉS de la primera "|"     (la variación)
SI NO, SI la clave empieza con "COMP-":
    VARIABLES = "COMBO"                                    (palabra fija)
SI NO, SI la clave es un número puro (regex ^\d+(\.\d+)?$):
    VARIABLES = ""                                         (era un ID sin variación → celda vacía)
EN OTRO CASO:
    VARIABLES = la clave tal cual
```

Resultado típico: la columna VARIABLES contiene tallas/colores (`"XL"`, `"Rojo"`), la palabra `"COMBO"`, o queda vacía.

---

## FASE 9 — Unificación de duplicados

**Equivalente VBA:** Módulo 5 (`UnificarDuplicados`).

Tras quitar el ID de la clave, pueden quedar filas visualmente idénticas (mismo producto, misma variación) que venían de IDs de producto distintos. Se fusionan:

```
llaveUnificación = VARIABLES + "|" + PRODUCTO

PARA cada fila del resumen:
    SI la llave ya existe:
        sumar columna por columna:  Cantidad_c(existente) += Cantidad_c(nueva)
    SI NO:
        registrar la fila como nueva
```

La suma trata celdas vacías como 0, y si el resultado de la suma es 0 la celda queda vacía (no se escribe `0`).

---

## FASE 10 — Limpieza de nombres (quitar paréntesis)

**Equivalente VBA:** Módulo 8 (`ProcesarResumen` + función `QuitarParentesis`).

Algunos nombres de producto traen sufijos numéricos entre paréntesis: `"Freidora Digital (2)"`. Se limpian con la expresión regular:

```
patrón:      \s*\(\d+\)\s*
reemplazo:   un espacio
luego:       trim del resultado
```

**Excepción absoluta:** las filas cuya columna VARIABLES sea `"COMBO"` **no se tocan**, porque en ellas los paréntesis `(1)`, `(2)` indican la composición del combo y son información esencial.

---

## FASE 11 — Orden final y limpieza de ceros

1. Ordenar las filas del Resumen **alfabéticamente por PRODUCTO** (A→Z).
2. Recorrer todas las celdas `Cantidad N`: todo valor `0` se convierte en **celda vacía**. El resumen queda visualmente despejado: solo se ven números donde hay órdenes reales.

Con esto la **Ruta A** está completa. La estructura final del Resumen es:

```
VARIABLES | PRODUCTO | Cantidad 1 | Cantidad 2 | ... | Cantidad cantMax
```

---

## FASE 12 — Reporte Ordenado (Ruta B)

**Equivalente VBA:** Módulo 7 (`Generar_Reporte_Ordenado_FAST`).

Es independiente del Resumen. Se construye desde los **registros limpios de la Fase 3** (incluye filas sin guía):

1. Tomar solo tres campos: `id` (ID de orden), `producto`, `cantidad`.
2. Renombrar encabezados a: `ID ORDEN`, `PRODUCTO`, `CANTIDAD`.
3. Ordenar por `PRODUCTO` ascendente y, dentro de cada producto, por `CANTIDAD` ascendente.

Sirve como listado de verificación línea a línea: cada orden individual con su producto y cantidad.

---

## FASE 13 — Órdenes Prioritarias (Ruta C)

**Equivalente VBA:** Módulos 10, 11 y 12 (`GenerarOrdenesPrioritarias_PRO` + formato + coloreado).

Se recorren **todos los registros originales** (no el resumen) buscando órdenes cuya guía fue generada pero que siguen sin moverse.

### 13.1 Parseo de la fecha de guía

El campo `fechaGuia` es texto. Se intenta parsear probando estos formatos **en este orden**, y el primero que funcione gana:

```
1. dd/mm/aaaa    (31/03/2026)
2. dd-mm-aaaa    (31-03-2026)
3. aaaa-mm-dd    (2026-03-31)
4. dd/mm/aa      (31/03/26)
```

Si ningún formato funciona (celda vacía, texto basura), la fila **se excluye silenciosamente** de Prioritarias.

### 13.2 Cálculo de días de retraso

```
díasRetraso = fechaDeHoy − fechaGuia        (en días calendario)
```

Ambas fechas se normalizan a medianoche (horas = 0) antes de restar, para evitar errores de ±1 día por zona horaria u hora del día.

**Filtro:** solo se conservan las filas con `díasRetraso >= 1`. Una guía generada hoy (0 días) no es prioritaria.

### 13.3 Clasificación y riesgo

Por cada orden que pasa el filtro:

```
ESTADO       = "URGENTE"         si díasRetraso == 1
             = "SUPER ATRASADA"  si díasRetraso > 1

RIESGO (20%) = redondear( valor × 0.20 )
```

El 20% representa la penalización/indemnización estimada sobre el valor de la orden. Se acumula un `totalRiesgo` con la suma de todos los riesgos.

### 13.4 Estructura y orden de salida

```
N° GUIA | PRODUCTO | VALOR | FECHA GUIA | DIAS RETRASO | ESTADO | RIESGO (20%)
```

Ordenado por `FECHA GUIA` **ascendente** (la más antigua arriba = lo más urgente primero).

---

## FASE 14 — Construcción del Excel de salida

Se genera un libro nuevo con tres hojas, en este orden: `Resumen`, `Reporte Ordenado`, `PRIORITARIAS`. Fuente de todo el libro: **Calibri**. Líneas de cuadrícula ocultas en las tres hojas.

### 14.1 Paleta de colores (códigos hex exactos)

| Uso | Hex |
|---|---|
| Verde corporativo (títulos, encabezados de cantidad, fila TOTAL) | `1B5E20` |
| Verde medio (fila TOTAL alternativa) | `2E7D32` |
| Verde claro (filas pares del Resumen) | `E8F5E9` |
| Gris oscuro (encabezados VARIACIÓN / PRODUCTO / Reporte Ordenado) | `263238` |
| Gris claro (filas impares) | `ECEFF1` |
| Ámbar claro (filas COMBO y prioritarias de 1 día) | `FFF8E1` |
| Rojo encabezado PRIORITARIAS | `B71C1C` |
| Rojo claro (prioritarias con 2+ días) | `FFEBEE` |
| Azul claro (filas pares del Reporte Ordenado) | `E3F2FD` |
| Bordes de todas las celdas (estilo thin) | `B0BEC5` |
| Subtítulo: fondo | `F5F5F5` |
| Subtítulo: texto | `455A64` |

### 14.2 Bloque de título (idéntico en las tres hojas)

| Fila | Contenido | Altura | Formato |
|---|---|---|---|
| 1 | `FulfillPro · {Título de la hoja}` | 36 px | Merge en todo el ancho, fondo verde `1B5E20`, Calibri 15 **negrita** blanca, alineado a la izquierda |
| 2 | Subtítulo con métricas + `Fecha: dd/mm/aaaa` | 20 px | Merge, fondo `F5F5F5`, Calibri 10 color `455A64` |
| 3 | (vacía, espaciador) | 14 px | Fondo `FAFAFA` |
| 4 | Encabezados de columnas | 26–28 px | Negrita blanca 10pt sobre el color del encabezado de cada hoja |

Los datos siempre empiezan en la **fila 5**. Paneles congelados en `A5` en las tres hojas.

### 14.3 Hoja `Resumen` — detalle completo

**Encabezados (fila 4):** `VARIACIÓN` y `PRODUCTO` con fondo gris `263238`; las columnas `Cant. 1` … `Cant. N` con fondo verde `1B5E20`.

**Filas de datos (desde la 5):**

- **Altura fija: 30 px** — suficiente para dos líneas de texto.
- **`wrap_text = True` (ajustar texto) en TODAS las celdas**, imprescindible en la columna PRODUCTO para que los nombres largos se ajusten en vez de cortarse. En openpyxl: `Alignment(wrap_text=True, vertical='center')` — usar `'center'`, no `'middle'` (error común).
- Color de fondo por fila: si VARIABLES = `"COMBO"` → ámbar `FFF8E1`; si no, alternar verde claro `E8F5E9` (filas pares) y gris claro `ECEFF1` (impares).
- Columna VARIACIÓN: centrada; texto verde `1B5E20` si tiene valor, gris `78909C` si está vacía; café y negrita si es COMBO.
- Columna PRODUCTO: alineada a la izquierda; negrita solo en combos.
- Columnas de cantidad: centradas, tamaño 11; **negrita verde** cuando hay valor; celdas sin valor quedan vacías (nunca `0`).

**Cierre de la hoja:**

1. Fila separadora de 4 px con fondo gris oscuro `263238` (barra visual).
2. Fila **TOTAL** (26 px): celda B con el texto `TOTAL — {suma} unidades`; cada columna de cantidad con la fórmula `=SUM(C5:C{últimaFila})` (fórmula real de Excel, no valor fijo, para que recalcule si el usuario edita). Fondo verde `2E7D32`, texto blanco negrita 11.
3. Nota al pie en cursiva gris 8pt: fecha/hora de generación + explicación de qué es un COMBO.

**Anchos de columna:** A = 12 · B = 38 · cantidades = 8.5 cada una.

**Configuración de impresión (la hoja debe salir perfecta en papel):**

```
Orientación:       vertical (portrait)
Papel:             A4 (código openpyxl: paperSize = 9)
Escala:            fitToPage = True, fitToWidth = 1, fitToHeight = 0
                   → se ajusta al ancho de una página; el alto fluye a las páginas necesarias
Área de impresión: A1 : {últimaColumna}{filaNota}
Márgenes:          izq/der 0.5"  ·  sup/inf 0.75"  ·  encabezado/pie 0.3"
Encabezado impreso (centro):  "FulfillPro — Resumen de Órdenes"  (Calibri Bold 14)
Pie de página:     izquierda "Fecha: dd/mm/aaaa" · centro "Página &P de &N" · derecha "Confidencial"
Filas repetidas:   1:4  → el título y los encabezados se reimprimen al inicio de CADA página
Cuadrícula:        oculta
```

### 14.4 Hoja `Reporte Ordenado`

Encabezados `ID ORDEN | PRODUCTO | CANTIDAD` en gris `263238`. Filas de 20 px alternando azul claro `E3F2FD` y gris claro. El **primer registro de cada producto** lleva el nombre en negrita (marca visual de dónde empieza cada grupo); ID en gris pequeño 9pt centrado; CANTIDAD en azul `1565C8` negrita centrada. Anchos: 14 · 38 · 10.

### 14.5 Hoja `PRIORITARIAS`

Encabezados en rojo `B71C1C`. Color por fila según urgencia: **2+ días → rojo claro `FFEBEE`**, **1 día → ámbar `FFF8E1`** (réplica del coloreo condicional del Módulo 12). Columnas VALOR y RIESGO con formato de moneda `$#,##0`, alineadas a la derecha. DÍAS RETRASO en negrita 11pt, rojo si 2+ días, naranja si 1. Al final, fila con `TOTAL RIESGO:` y la fórmula `=SUM(G5:G{última})` en formato moneda, fondo rojo, texto blanco. Si no hay ninguna orden atrasada, se escribe la leyenda *"Sin órdenes atrasadas para la fecha de hoy."* en cursiva gris. Anchos: 18 · 36 · 12 · 12 · 13 · 16 · 13.

### 14.6 Detalles finales del libro

- Color de pestaña: Resumen verde `1B5E20` · Reporte Ordenado gris `263238` · PRIORITARIAS rojo `B71C1C`.
- Nombre del archivo de salida: `FulfillPro_Resultado_AAAA-MM-DD.xlsx`.
- Si se usaron fórmulas (`=SUM`), recalcular el libro antes de entregar para que los valores estén materializados.

---

## Tabla de equivalencias VBA → Sistema nuevo

| Módulo VBA original | Fase en el sistema nuevo |
|---|---|
| M1 `FiltrarYOrdenarColumnaAD` | Fase 4 (orden por producto) |
| M2 `AgruparPorIDyCantidad_ConCompuestas` | Fases 5, 6 y 7 |
| M3 `OrdenarResumenAutomatico` | Fase 8 + orden de Fase 11 |
| M4 `OrganizarResumenFinal` | Fase 8 (reglas número puro / COMP→COMBO) |
| M5 `UnificarDuplicados` | Fase 9 |
| M6 `Formato_Estetico_Resumen` | Fase 14.3 |
| M7 `Generar_Reporte_Ordenado_FAST` | Fase 12 |
| M8 `ProcesarResumen` + `QuitarParentesis` | Fase 10 |
| M9 `EJECUTAR_TODO` | Orquestador: Fases 1–12 + 14 |
| M10 `GenerarOrdenesPrioritarias_PRO` | Fase 13 |
| M11 `FormatoHojaPrioritarias` | Fase 14.5 |
| M12 `ColorearPorAntiguedadReal` | Fase 14.5 (colores por días) |
| M13 `Ordenes_atrasadas` | Orquestador: Fase 13 + 14.5 |

**Diferencia clave con el VBA:** las dos macros orquestadoras (M9 y M13) eran ejecuciones separadas; en el sistema nuevo **todo corre en una sola pasada** y el usuario recibe las tres hojas de una vez.

---

## Casos borde y validaciones (checklist de robustez)

1. **Archivo vacío** → error claro, no crash.
2. **Falta PRODUCTO o NÚMERO GUIA** → error nombrando la columna exacta que falta.
3. **Faltan columnas opcionales** → el sistema degrada con elegancia (sin variaciones / prioritarias vacías / riesgo en $0).
4. **Guía vacía** → la fila no entra al Resumen pero sí al Reporte Ordenado.
5. **Cantidad 0, negativa o no numérica** → se trata como 1 en el resumen.
6. **Fecha de guía ilegible** → la fila se omite de Prioritarias sin detener el proceso.
7. **`"nan"` literal en variación** → se trata como vacío.
8. **IDs/guías largos** → siempre como texto, nunca como número (evita notación científica).
9. **Cantidad absurdamente grande** → aplicar tope de columnas (`cantMax ≤ 60` recomendado).
10. **Nombres de producto muy largos** → resueltos por `wrap_text` + altura de fila 30 px.
11. **Mismo producto bajo IDs distintos** → resuelto por la unificación de la Fase 9.
12. **Combo con el mismo producto repetido** → sigue siendo combo (2 líneas = combo, sin importar el contenido).

---

*Documento generado a partir del motor en producción. Cualquier implementación que siga estas 14 fases en este orden producirá un resultado idéntico al sistema actual.*
