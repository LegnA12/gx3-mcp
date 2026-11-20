# gx3-mcp: servidor MCP para proyectos GX Works3 (.gx3)

Servidor **MCP** (Model Context Protocol) en Python que permite a Claude (Claude Code o cualquier cliente MCP) **leer y editar directamente proyectos Mitsubishi GX Works3** (archivos `.gx3`, PLC MELSEC iQ-F / FX5U): listar programas, leer y editar código ST, leer ladder como texto, consultar etiquetas y agregar o borrar renglones ladder. También **lee en vivo el PLC** por Ethernet (SLMP o Modbus/TCP), en modo solo lectura.

El formato `.gx3` no está documentado. Se entendió analizando proyectos reales (ingeniería inversa) y se validó abriendo los resultados en GX Works3, donde convirtieron y compilaron correctamente.

> [!WARNING]
> **Proyecto no oficial, sin relación con Mitsubishi Electric.**
> MELSEC y GX Works3 son marcas de Mitsubishi Electric.
> **Úsalo bajo tu propio riesgo.** Nunca descargues a un PLC que controle maquinaria real un programa que no hayas probado antes (por ejemplo en GX Simulator3).

## Contenido

- [Qué puede hacer](#qué-puede-hacer)
- [Qué no puede hacer todavía](#qué-no-puede-hacer-todavía)
- [Requisitos e instalación](#requisitos-e-instalación)
- [Ejemplos de uso](#ejemplos-de-uso)
- [Lectura en vivo del PLC](#lectura-en-vivo-del-plc)
- [Cómo funciona](#cómo-funciona)
- [Formato .gx3 en resumen](#formato-gx3-en-resumen)
- [Seguridad y validación](#seguridad-y-validación)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Hoja de ruta](#hoja-de-ruta)
- [Contribuir](#contribuir)
- [Licencia](#licencia)

## Qué puede hacer

| Herramienta | Qué hace | Ejemplo de petición |
|---|---|---|
| `listar_programas` | Lista los programas del `.gx3`: id, lenguaje (ST o LD) y primera línea ST. | "Qué programas tiene el proyecto?" |
| `leer_st` | Devuelve el código ST completo de un programa, con líneas numeradas. | "Muéstrame el programa ST de la bomba." |
| `editar_st` | Reemplaza un texto (debe aparecer una sola vez) por otro y guarda en un archivo de salida. | "Cambia el límite `100` por `150` en ese ST." |
| `leer_ld` | Muestra un programa ladder como texto: comentarios, renglones dibujados (`R1`, `R2`...) y `END`. | "Léeme el ladder del programa principal." |
| `leer_etiquetas` | Lista etiquetas globales, estructuras con sus miembros, etiquetas locales por programa o FB y los dispositivos asignados a mano. | "Qué etiquetas globales hay y a qué dispositivo apuntan?" |
| `agregar_renglon_ld` | Agrega un renglón con comentario, al final (antes de `END`) o antes de un renglón dado. | "Agrega arranque/paro de Y0 con X0, X1 y enclavamiento." |
| `borrar_renglon_ld` | Borra el renglón `R<n>` y el comentario que tiene encima. | "Borra el renglón R3." |
| `revisar_ld` | Detecta dobles bobinas y salidas usadas con `OUT` y con `SET`/`RST` a la vez. | "Revisa si hay dobles bobinas." |
| `leer_plc` | Lee en vivo dispositivos del PLC por SLMP o Modbus/TCP (**solo lectura**). | "Lee X0 a X7, Y0 a Y4 y el valor actual de T0." |

Lo que `agregar_renglon_ld` sabe escribir:

- Contactos: `X0` (NA), `/X0` (NC), `↑X0` (flanco de subida), `↓X0` (flanco de bajada) y comparaciones (`>= D10 K100`, con `=`, `<>`, `>`, `<`, `>=`, `<=`).
- Salidas: `Y0` (bobina), `SET M0`, `RST M0`, `OUT T0 K30` (temporizador de 100 ms), `MOV K0 D10`, `INCP D10`.
- Contactos en paralelo (rama OR) en la primera columna y varias columnas en serie (AND).

## Qué no puede hacer todavía

- **No escribe etiquetas ni estructuras** (solo las lee). Los renglones nuevos usan dispositivos directos.
- Las ramas **OR solo existen en la primera columna**; no hay ramas a mitad de renglón.
- Contactos y bobinas solo con **dispositivos directos** (`X0`, `M10`, `D5`), no con etiquetas.
- **Sin contadores.**
- **Sin llamadas a FB desde ladder** al escribir (sí se leen).
- **Borrar renglones todavía no está validado en GX Works3** (agregar sí lo está). Revisa el resultado antes de usarlo.
- Los nombres de programa salen de `ConvertData` solo si el proyecto **ya fue convertido**. Si no, algunos programas aparecen solo con su hash.
- **El acceso al PLC es solo de lectura**: no escribe dispositivos, no fuerza entradas y no descarga programas (eso se hace desde GX Works3).
- **Modbus/TCP solo está probado contra un esclavo simulado**; SLMP sí está probado con un FX5U real. Modbus asume la asignación inicial "FX5 dedicado".

## Requisitos e instalación

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Un cliente MCP (por ejemplo Claude Code)

```
cd <ruta>
uv venv
uv pip install mcp
```

Registro en Claude Code (alcance de usuario):

```
claude mcp add --scope user gx3 -- <ruta>\.venv\Scripts\python.exe <ruta>\gx3mcp\gx3_mcp.py
```

Reinicia Claude Code y comprueba con `/mcp` que `gx3` aparece conectado.

## Ejemplos de uso

### Diálogo típico

| Le pides a Claude | Herramienta que usa |
|---|---|
| "Qué programas hay en `<ruta>\Proyecto.gx3`?" | `listar_programas` |
| "Léeme el ladder del programa `a1b2c3...`" | `leer_ld` (y `leer_etiquetas` para resolver nombres) |
| "Agrega un arranque/paro del Motor con X0, X1, Y0 y enclavamiento, antes del renglón 2" | `agregar_renglon_ld` |
| "Revisa que no haya dobles bobinas" | `revisar_ld` |
| "En el ST, cambia `Limite := 100` por `Limite := 150`" | `leer_st` y luego `editar_st` |

Todas las escrituras van a un **archivo de salida** (`ruta_salida`); el original no se toca sin respaldo.

### Salida de `leer_ld`

Arranque y paro con enclavamiento:

```
// Arranque y paro con enclavamiento
R1  |--[X0]--+--[X1]-----(Y0)--
    |--[Y0]--+
```

Leyenda:

| Notación | Significado |
|---|---|
| `[X0]` | Contacto normalmente abierto (NO) |
| `[/X0]` | Contacto normalmente cerrado (NC) |
| `[↑X0]` | Flanco de subida |
| `[↓X0]` | Flanco de bajada |
| `(Y0)` | Bobina |
| `[SET M0]` | Instrucción de salida |
| `[>= D10 K5]` | Contacto de comparación |
| `+` | Unión de ramas |

X e Y se muestran en octal, como en GX Works3.

## Lectura en vivo del PLC

`leer_plc` consulta el PLC por Ethernet mientras está en RUN, para comprobar que el programa hace lo que se espera. Es **solo lectura**: usa las funciones de lectura de cada protocolo (SLMP `0401`; Modbus FC01, FC02 y FC03) y nunca escribe en el PLC.

| | SLMP | Modbus/TCP |
|---|---|---|
| Qué es | Protocolo de Mitsubishi (trama 3E binaria) | Estándar industrial abierto |
| Dispositivos | Todos (X, Y, M, L, B, F, SM, D, W, R, SD, TS, TN, CS, CN) | Solo los asignados en la tabla Modbus del PLC |
| Lectura por petición | Hasta 960 palabras o 3584 bits | Hasta 125 registros o 2000 bits |
| Uso recomendado | Diagnóstico y pruebas | Integración con SCADA o equipos de terceros |
| Estado | Probado con un FX5U real | Probado contra un esclavo simulado |

### Configuración en GX Works3

*Parámetro → FX5UCPU → Parámetro de módulo → Puerto Ethernet*:

- **SLMP:** en *Basic Settings*, Communication Data Code = **Binary**. En *External Device Configuration*, agrega un **SLMP Connection Module** (TCP, por ejemplo puerto 5000; evita 5549 a 5569, reservados por el sistema).
- **Modbus/TCP:** en *External Device Configuration*, agrega un **MODBUS/TCP Connection Module** (puerto 502) y deja la asignación de dispositivos inicial (patrón "FX5 dedicado").

Escribe los parámetros al PLC y **reinícialo**: los parámetros Ethernet se aplican al arrancar. Los dos protocolos pueden convivir; comparten el límite de 8 conexiones Ethernet del FX5.

### Ejemplo

```
leer_plc("192.168.3.250", 5000, ["X0:8", "Y0:5", "M0", "TN0"], protocolo="slmp")

X0 = 1   X1 = 1   X2 = 0 ...
Y0 = 1   ← motor en marcha
M0 = 0
TN0 = 30 ← valor actual del temporizador T0 (3.0 s con base de 100 ms)
```

`nombre:cantidad` lee varios dispositivos seguidos. X e Y van en octal, como en GX Works3. Todas las lecturas de una llamada usan **una sola conexión TCP**: el módulo SLMP atiende un cliente por conexión y rechaza las conexiones nuevas que llegan muy seguidas.

```mermaid
sequenceDiagram
    participant C as Claude
    participant S as Servidor gx3
    participant P as PLC FX5U
    C->>S: leer_plc ip, puerto, dispositivos
    S->>S: Validar y traducir dispositivos
    S->>P: Abrir una conexion TCP
    loop por cada dispositivo
        S->>P: Peticion de lectura SLMP 0401 o Modbus FC01/02/03
        P-->>S: Respuesta con los valores, en el END del scan
    end
    S->>P: Cerrar la conexion
    S-->>C: Valores con nombres como en GX Works3
```

## Cómo funciona

### Arquitectura

```mermaid
flowchart LR
    U["Usuario"] --> C["Claude Code - cliente MCP"]
    C -->|"stdio - herramientas MCP"| S["Servidor MCP gx3"]
    S --> E["etiquetas.py - resuelve nombres"]
    S --> Z[".gx3 - archivo ZIP"]
    E --> Z
    Z --> L["hash_LDDB.db - LadderBlocks"]
    Z --> T["hash_STDB.db - StBlocks"]
    Z --> M["hash_MilDB.db - IL generado"]
    Z --> D["LabelData.db - etiquetas"]
    Z -->|"el usuario abre y convierte"| G["GX Works3"]
    G --> SIM["GX Simulator3"]
    SIM -->|"solo tras probar"| P["PLC FX5U"]
    S -->|"leer_plc: SLMP o Modbus TCP, solo lectura"| P
```

### Flujo de lectura de `leer_ld`

```mermaid
flowchart TD
    A["Abrir el ZIP .gx3"] --> B["Extraer hash_LDDB.db a carpeta temporal"]
    B --> C["Leer LadderBlocks ordenados por pos"]
    C --> D["Separar bloques V1: cadenas y arbol"]
    D --> E["Parsear el arbol: nodos y atributos"]
    E --> F["Resolver _lid con LabelData.db"]
    F --> G["Dibujar la rejilla como texto"]
    G --> H["Numerar renglones R1, R2 ..."]
```

### Flujo de escritura de `agregar_renglon_ld` y `editar_st`

```mermaid
flowchart TD
    A["Validar la entrada"] --> B["Codificar el renglon o el texto ST"]
    B --> C["Extraer la base SQLite a carpeta temporal"]
    C --> D["Modificar la base SQLite"]
    D --> E{"Revision de integridad SQLite"}
    E -->|"falla"| X["Abortar sin tocar el original"]
    E -->|"ok"| F["Reconstruir el ZIP"]
    F --> G{"Revision de integridad ZIP"}
    G -->|"falla"| X
    G -->|"ok"| H["Respaldo .bak con fecha y hora"]
    H --> I["Reemplazo atomico del archivo"]
    I --> J["El usuario ejecuta Convertir todo en GX Works3"]
    J --> K["Probar en GX Simulator3"]
```

El renglón agregado queda **sin convertir** (`translated=0`). Al convertir, GX Works3 solo cambia ese campo a `1` y genera el IL correspondiente.

### Cómo se aprendió el formato (ingeniería inversa)

```mermaid
flowchart TD
    A["Proyectos reales .gx3"] --> B["Extraer y comparar las bases SQLite"]
    B --> C["Hipotesis sobre el formato"]
    C --> D["Generar un renglon con el servidor"]
    D --> E["Comparar byte a byte con renglones reales"]
    E --> F["Abrir en GX Works3"]
    F --> G{"Convirtio y compilo?"}
    G -->|"si"| H["Validado"]
    G -->|"no"| C
    X["Experimento de diferencias: el usuario crea algo en GX Works3"] --> Y["El servidor compara antes y despues"]
    Y --> B
```

El **experimento de diferencias** es la técnica clave para lo que aún no se entiende: el usuario guarda el proyecto, crea un elemento concreto en GX Works3 (una etiqueta, un contador), vuelve a guardar, y el servidor compara ambos `.gx3` para ver exactamente qué filas y bytes cambiaron.

### Modelo de datos de `LabelData.db`

```mermaid
erDiagram
    LabelTbl ||--o{ RowTbl : "LabelID"
    RowTbl ||--o{ ColumnDataTbl : "LabelID y RowID"
    RowTbl ||--o{ ArrayInfoTbl : "LabelID y RowID"
    RowTbl ||--o{ RowRelationTbl : "LabelID y RowID"
    LabelTbl ||--o{ RowRelationTbl : "RelatedLabelID"
    RowTbl ||--o{ InstanceLayerTbl : "RootLabelID y RootRowID"
    InstanceLayerTbl ||--o| DeviceAssignTbl : "instancia"

    LabelTbl {
        text LabelID
        int LabelTypeID
    }
    RowTbl {
        text LabelID
        int RowID
        int RowNo
        int VarType
        int BitCount
    }
    ColumnDataTbl {
        text LabelID
        int RowID
        int ColumnID
        text ColumnStrValue
        int ColumnIntValue
    }
    RowRelationTbl {
        text LabelID
        int RowID
        text RelatedLabelID
    }
    ArrayInfoTbl {
        text LabelID
        int RowID
        int ArrayNo
        int ArrayCount
        int StartNo
    }
    InstanceLayerTbl {
        int InstanceID
        text RootLabelID
        int RootRowID
        int LayerLevel
        int ParentInstanceID
    }
    DeviceAssignTbl {
        text MELSECDevice
        int ManualSet
    }
```

Resumen de la resolución de etiquetas (detalle en `conocimiento/formato_gx3.md`):

- Una referencia `_lid/<LabelID>/<RowID>` en el ladder apunta a una fila de `RowTbl`; sus campos están en `ColumnDataTbl`: columna 1 clase, 2 nombre, 3 tipo de dato, 7 dispositivo asignado a mano.
- `LabelTypeID` indica el dueño de la tabla: 5121 programa, 5122 global, 5123 estructura, 5125 FUN, 5126 FB.
- `RowID` es estable; `RowNo` es solo el orden visible en el editor.
- Los tipos de usuario (estructuras, FB) se enlazan con `RowRelationTbl`; los arreglos tienen una fila por dimensión en `ArrayInfoTbl`.

## Formato .gx3 en resumen

Un `.gx3` es un **ZIP**. Cada POU es una base SQLite:

| Archivo | Contenido |
|---|---|
| `<hash>_LDDB.db` | Ladder (tabla `LadderBlocks`) |
| `<hash>_STDB.db` | ST en texto plano (tabla `StBlocks`) |
| `<hash>_MilDB.db` | IL que genera GX Works3 al convertir |
| `LabelData.db` | Etiquetas globales, locales, estructuras y FB |

Los bloques ladder se serializan como `V1:<n>:<longitudes>:<cadenas>:<arbol>`. Cada `#` del árbol toma la siguiente cadena. Ejemplos:

| Elemento | Serialización |
|---|---|
| Contacto NO | `ct{op=a}` |
| Contacto NC | `ct{op=b}` |
| Bobina | `cl{op=c}` |
| Dispositivo | `d{s=X:a=0}` |

X e Y son octales y se guardan leyendo sus dígitos como hexadecimal (por ejemplo `Y10` se guarda con dirección `0x10`).

`LadderBlocks.blocktype`: 0 renglón, 1 comentario, 4 llamada a FB, 5 `END`.

## Seguridad y validación

**Validación.** Los renglones que escribe el servidor son idénticos byte a byte a los renglones reales. GX Works3 los convirtió y compiló; al convertir solo cambia `translated` de 0 a 1.

**Salvaguardas del servidor:**

- Nunca sobrescribe sin crear antes un respaldo `.bak` con marca de fecha y hora.
- Comprueba la integridad de SQLite y del ZIP antes de guardar.
- Escribe de forma atómica.
- `editar_st` exige que el texto a reemplazar aparezca una sola vez.
- `agregar_renglon_ld` se niega si el programa no tiene `END`.

**Flujo recomendado antes de tocar una máquina real:**

1. Abre el `.gx3` generado en GX Works3.
2. Ejecuta **Convertir todo** y revisa que no haya errores.
3. Prueba en **GX Simulator3**.
4. Solo entonces, descarga al PLC, con la máquina en condición segura.

## Estructura del repositorio

```
.
├── gx3mcp/                 Todo el código Python
│   ├── gx3_mcp.py              Servidor MCP y herramientas
│   ├── etiquetas.py            Resuelve _lid/<LabelID>/<RowID> a nombres de etiqueta
│   ├── plc_slmp.py             Cliente SLMP 3E binario (solo lectura), con autoprueba
│   ├── plc_modbus.py           Cliente Modbus/TCP (solo lectura), con autoprueba
│   ├── test_gx3_mcp.py         Pruebas de lectura/edición ST y utilidades
│   └── test_ladder.py          Pruebas de codificación y lectura de ladder
└── conocimiento/
    └── formato_gx3.md          Formato .gx3 obtenido por ingeniería inversa
```

> Las pruebas necesitan **tus propios proyectos `.gx3`**, que no se incluyen en el repositorio:
>
> ```
> python gx3mcp/test_gx3_mcp.py <proyecto con programas ST>.gx3
> python gx3mcp/test_ladder.py <carpeta con .gx3> <proyecto LD>.gx3 [<proyecto con etiquetas>.gx3]
> python gx3mcp/etiquetas.py <carpeta con .gx3>
> ```
>
> Los clientes del PLC traen su propia autoprueba, que usa un PLC simulado y no necesita hardware:
>
> ```
> python gx3mcp/plc_slmp.py
> python gx3mcp/plc_modbus.py
> ```

Para el juego de instrucciones y la comunicación del FX5, consulta los manuales oficiales de Mitsubishi Electric (*MELSEC iQ-F FX5 Programming Manual* y *FX5 User's Manual (Communication)*).

## Hoja de ruta

- [ ] Escribir etiquetas y estructuras (usando el experimento de diferencias).
- [ ] Ramas OR a mitad de renglón.
- [ ] Contadores.
- [ ] Llamadas a FB desde ladder.
- [ ] Validar el borrado de renglones en GX Works3.
- [x] Lectura en vivo del PLC por SLMP (probada con un FX5U real).
- [x] Lectura por Modbus/TCP (probada contra un esclavo simulado).
- [ ] Probar Modbus/TCP con un PLC real.
- [ ] Escritura controlada de dispositivos (con confirmación explícita y lista de dispositivos permitidos).

## Contribuir

Las contribuciones son bienvenidas.

- Abre un issue describiendo qué quieres cambiar antes de enviar un PR grande.
- Todo lo que escriba un `.gx3` debe validarse así: comparar con un caso real creado en GX Works3 y comprobar que **convierte y compila**. Indica en el PR qué versión de GX Works3 usaste.
- Documenta los hallazgos del formato en `conocimiento/formato_gx3.md`, marcando como verificado solo lo que respaldan varios ejemplos.
- **No subas proyectos `.gx3` reales ni datos de terceros**: contienen información de planta y de cliente. Usa proyectos de ejemplo propios y anonimizados.

## Licencia

[MIT](LICENSE). MELSEC y GX Works3 son marcas de Mitsubishi Electric Corporation; este proyecto no está afiliado a ella.
